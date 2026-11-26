"""Interactive puzzle slider captcha service.

Generates retina-resolution nautical twilight illustrations with anti-aliased
recessed cutout slots and matching puzzle pieces. Validates drag coordinates,
timing, and trajectory before issuing single-use captcha tokens.
"""

from __future__ import annotations

import base64
import io
import math
import random
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter


@dataclass
class CaptchaChallenge:
    challenge_id: str
    bg_image: str
    piece_image: str
    y: int
    width: int
    height: int
    piece_width: int
    piece_height: int


_THEMES = (
    {
        "sky_top": (15, 29, 48),
        "sky_mid": (38, 69, 98),
        "sky_low": (96, 130, 152),
        "glow": (244, 212, 162),
        "sun": (252, 238, 204),
        "far_ridge": (29, 54, 78, 175),
        "near_ridge": (18, 37, 56, 225),
        "sea_top": (16, 40, 62),
        "sea_bottom": (30, 66, 94),
        "glint": (245, 220, 175),
        "grid": (210, 228, 242),
    },
    {
        "sky_top": (20, 31, 56),
        "sky_mid": (58, 78, 114),
        "sky_low": (146, 128, 134),
        "glow": (246, 194, 156),
        "sun": (254, 228, 198),
        "far_ridge": (36, 49, 76, 175),
        "near_ridge": (21, 32, 52, 225),
        "sea_top": (19, 36, 58),
        "sea_bottom": (38, 62, 92),
        "glint": (248, 206, 170),
        "grid": (222, 226, 240),
    },
    {
        "sky_top": (14, 38, 56),
        "sky_mid": (42, 90, 112),
        "sky_low": (118, 164, 172),
        "glow": (230, 236, 204),
        "sun": (248, 248, 228),
        "far_ridge": (24, 58, 78, 175),
        "near_ridge": (15, 39, 56, 225),
        "sea_top": (14, 42, 58),
        "sea_bottom": (28, 74, 94),
        "glint": (230, 242, 224),
        "grid": (210, 236, 238),
    },
)


def _lerp_rgb(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        round(c1[0] + (c2[0] - c1[0]) * t),
        round(c1[1] + (c2[1] - c1[1]) * t),
        round(c1[2] + (c2[2] - c1[2]) * t),
    )


class CaptchaService:
    def __init__(self, *, ttl_seconds: int = 180, token_ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self.token_ttl_seconds = token_ttl_seconds
        self._lock = threading.Lock()
        self._challenges: dict[str, dict[str, Any]] = {}
        self._tokens: dict[str, dict[str, Any]] = {}

    def _prune(self, now: float) -> None:
        expired_challenges = [k for k, v in self._challenges.items() if v["expires_at"] <= now]
        for k in expired_challenges:
            self._challenges.pop(k, None)
        expired_tokens = [k for k, v in self._tokens.items() if v["expires_at"] <= now]
        for k in expired_tokens:
            self._tokens.pop(k, None)

    @staticmethod
    def _draw_jigsaw_shape(pw: int, ph: int, expand_px: float, ss: int = 4) -> Image.Image:
        """Render a smooth jigsaw silhouette at 4x supersampling for crisp anti-aliasing."""
        w, h = pw * ss, ph * ss
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        pad = 12 * ss
        tab_r = 8 * ss
        d = round(expand_px * ss)

        draw.rounded_rectangle(
            [pad - d, pad - d, w - pad + d, h - pad + d],
            radius=max(1, 5 * ss + d),
            fill=255,
        )
        cx, cy = w // 2, h // 2
        # Top convex tab
        draw.ellipse([cx - tab_r - d, pad - tab_r - d, cx + tab_r + d, pad + tab_r + d], fill=255)
        # Right convex tab
        draw.ellipse([w - pad - tab_r - d, cy - tab_r - d, w - pad + tab_r + d, cy + tab_r + d], fill=255)
        # Bottom concave notch
        hole_r = max(1, tab_r - d)
        draw.ellipse([cx - hole_r, h - pad - hole_r, cx + hole_r, h - pad + hole_r], fill=0)
        return mask

    def _create_jigsaw_assets(self, pw: int, ph: int, scale: int) -> tuple[Image.Image, Image.Image, Image.Image, Image.Image]:
        """Return (mask, inner_shadow_mask, piece_rim_mask, slot_rim_mask) at output scale."""
        ss = 4
        target_size = (pw * scale, ph * scale)

        base_4x = self._draw_jigsaw_shape(pw, ph, 0.0, ss)
        piece_outer_4x = self._draw_jigsaw_shape(pw, ph, 0.7, ss)
        piece_inner_4x = self._draw_jigsaw_shape(pw, ph, -0.9, ss)
        piece_rim_4x = ImageChops.subtract(piece_outer_4x, piece_inner_4x)

        slot_outer_4x = self._draw_jigsaw_shape(pw, ph, 0.6, ss)
        slot_inner_4x = self._draw_jigsaw_shape(pw, ph, -0.7, ss)
        slot_rim_4x = ImageChops.subtract(slot_outer_4x, slot_inner_4x)

        mask = base_4x.resize(target_size, Image.Resampling.LANCZOS)
        piece_rim = piece_rim_4x.resize(target_size, Image.Resampling.LANCZOS)
        slot_rim = slot_rim_4x.resize(target_size, Image.Resampling.LANCZOS)

        # Crescent shadow shifted down-right to suggest an indented cutout
        shift_px = 2 * scale
        shifted = ImageChops.offset(mask, shift_px, shift_px)
        shift_draw = ImageDraw.Draw(shifted)
        shift_draw.rectangle([0, 0, target_size[0], shift_px - 1], fill=0)
        shift_draw.rectangle([0, 0, shift_px - 1, target_size[1]], fill=0)
        inner_shadow = ImageChops.subtract(mask, shifted)

        return mask, inner_shadow, piece_rim, slot_rim

    @staticmethod
    def _draw_chart(width: int, height: int, scale: int, rng: random.Random) -> Image.Image:
        """Draw an atmospheric maritime twilight illustration with clear local contrast."""
        theme = rng.choice(_THEMES)
        w, h = width * scale, height * scale
        horizon = rng.randint(100, 114)
        horizon_px = horizon * scale

        image = Image.new("RGB", (w, h))
        pixels = image.load()

        for y in range(h):
            if y <= horizon_px:
                t = y / max(1, horizon_px)
                if t < 0.65:
                    row_rgb = _lerp_rgb(theme["sky_top"], theme["sky_mid"], t / 0.65)
                else:
                    row_rgb = _lerp_rgb(theme["sky_mid"], theme["sky_low"], (t - 0.65) / 0.35)
            else:
                t = (y - horizon_px) / max(1, h - horizon_px)
                row_rgb = _lerp_rgb(theme["sea_top"], theme["sea_bottom"], t)
            for x in range(w):
                pixels[x, y] = row_rgb

        draw = ImageDraw.Draw(image, "RGBA")
        s = lambda v: round(v * scale)

        # Soft Gaussian atmospheric glow behind celestial body (no stepped rings)
        sun_x = rng.randint(74, width - 74)
        sun_y = rng.randint(34, 58)
        glow_rgb = theme["glow"]
        glow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow_layer)
        gdraw.ellipse(
            (s(sun_x - 52), s(sun_y - 52), s(sun_x + 52), s(sun_y + 52)),
            fill=(*glow_rgb, 95),
        )
        gdraw.ellipse(
            (s(sun_x - 26), s(sun_y - 26), s(sun_x + 26), s(sun_y + 26)),
            fill=(*glow_rgb, 130),
        )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=18 * scale))
        base_rgba = image.convert("RGBA")
        base_rgba.alpha_composite(glow_layer)
        image = base_rgba.convert("RGB")
        draw = ImageDraw.Draw(image, "RGBA")

        draw.ellipse(
            (s(sun_x - 12), s(sun_y - 12), s(sun_x + 12), s(sun_y + 12)),
            fill=(*theme["sun"], 244),
        )

        # Delicate upper-sky stars (kept clear of moon disc)
        for _ in range(20):
            sx = rng.randint(14, width - 14)
            sy = rng.randint(10, horizon - 38)
            if math.hypot(sx - sun_x, sy - sun_y) < 26:
                continue
            sr = rng.choice((0.7, 0.9, 1.2))
            alpha = rng.randint(95, 195)
            draw.ellipse(
                (s(sx - sr), s(sy - sr), s(sx + sr), s(sy + sr)),
                fill=(242, 246, 255, alpha),
            )

        # Soft twilight mist band along the mountains
        mist_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        mdraw = ImageDraw.Draw(mist_layer)
        mdraw.rectangle((0, s(horizon - 18), w, s(horizon + 4)), fill=(*glow_rgb, 42))
        mist_layer = mist_layer.filter(ImageFilter.GaussianBlur(radius=10 * scale))
        base_rgba = image.convert("RGBA")
        base_rgba.alpha_composite(mist_layer)
        image = base_rgba.convert("RGB")
        draw = ImageDraw.Draw(image, "RGBA")

        # Smooth layered mountain/island ridges using sinusoidal harmonics
        phase1 = rng.uniform(0, math.tau)
        phase2 = rng.uniform(0, math.tau)
        far_pts = [(0, s(horizon + 4))]
        for x in range(0, width + 6, 4):
            h_offset = (
                18 * math.sin(x * 0.022 + phase1)
                + 9 * math.cos(x * 0.048 + phase2)
                + 5 * math.sin(x * 0.09)
            )
            ry = min(horizon, horizon - 14 - abs(h_offset) * 0.68)
            far_pts.append((s(x), s(ry)))
        far_pts += [(w, s(horizon + 4)), (0, s(horizon + 4))]
        draw.polygon(far_pts, fill=theme["far_ridge"])

        phase3 = rng.uniform(0, math.tau)
        near_pts = [(0, s(horizon + 4))]
        for x in range(0, width + 6, 4):
            h_offset = (
                12 * math.cos(x * 0.026 + phase3)
                + 6 * math.sin(x * 0.055 + phase1)
            )
            ry = min(horizon + 1, horizon - 6 - max(0.0, h_offset) * 0.72)
            near_pts.append((s(x), s(ry)))
        near_pts += [(w, s(horizon + 4)), (0, s(horizon + 4))]
        draw.polygon(near_pts, fill=theme["near_ridge"])

        # Crisp luminous horizon mist hairline
        draw.line([(0, s(horizon)), (w, s(horizon))], fill=(*glow_rgb, 110), width=scale)

        # Organic shimmering moonpath reflections on water (broken segments + varied opacity)
        glint_rgb = theme["glint"]
        shimmer_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shimmer_layer)
        for idx in range(14):
            wy = horizon + 5 + idx * 5.2
            if wy >= height - 6:
                break
            half_w = 10 + idx * 1.6
            alpha = max(35, 165 - idx * 9)
            segs = rng.randint(1, 3)
            for _ in range(segs):
                seg_center = sun_x + rng.uniform(-half_w * 0.55, half_w * 0.55)
                seg_len = rng.uniform(6, half_w * 0.85)
                sdraw.line(
                    [(s(seg_center - seg_len / 2), s(wy)), (s(seg_center + seg_len / 2), s(wy))],
                    fill=(*glint_rgb, alpha),
                    width=max(1, round(scale * 1.3)),
                )
        shimmer_layer = shimmer_layer.filter(ImageFilter.GaussianBlur(radius=0.8 * scale))
        base_rgba = image.convert("RGBA")
        base_rgba.alpha_composite(shimmer_layer)
        image = base_rgba.convert("RGB")
        draw = ImageDraw.Draw(image, "RGBA")

        # Faint ambient water ripples across full width
        for row in range(6):
            ry = horizon + 10 + row * 11
            rx = rng.randint(18, width - 90)
            rw = rng.randint(36, 82)
            draw.line([(s(rx), s(ry)), (s(rx + rw), s(ry))], fill=(210, 230, 244, 34), width=scale)

        # Refined navigation arc & sailboat silhouette as high-contrast visual anchor
        ship_x = rng.randint(68, width - 68)
        while abs(ship_x - sun_x) < 28:
            ship_x = rng.randint(68, width - 68)
        ship_y = horizon + rng.randint(16, 28)

        arc_box = (s(ship_x - 75), s(horizon - 65), s(ship_x + 75), s(horizon + 45))
        draw.arc(arc_box, 195, 345, fill=(*theme["grid"], 42), width=scale)

        draw.polygon(
            [
                (s(ship_x - 20), s(ship_y)),
                (s(ship_x + 22), s(ship_y)),
                (s(ship_x + 15), s(ship_y + 6)),
                (s(ship_x - 14), s(ship_y + 6)),
            ],
            fill=(10, 20, 32, 235),
        )
        draw.polygon(
            [(s(ship_x + 1), s(ship_y - 26)), (s(ship_x + 1), s(ship_y - 2)), (s(ship_x + 17), s(ship_y - 2))],
            fill=(238, 243, 246, 215),
        )
        draw.polygon(
            [(s(ship_x - 1), s(ship_y - 21)), (s(ship_x - 1), s(ship_y - 2)), (s(ship_x - 13), s(ship_y - 2))],
            fill=(196, 214, 228, 175),
        )
        draw.line([(s(ship_x), s(ship_y - 28)), (s(ship_x), s(ship_y))], fill=(242, 246, 250, 230), width=scale)

        return image

    def create_challenge(self, *, client_ip: str = "") -> CaptchaChallenge:
        w, h = 360, 180
        pw, ph = 56, 56
        target_x = secrets.randbelow(207) + 78
        target_y = secrets.randbelow(76) + 27
        now = time.time()
        scale = 2
        rng = random.Random(secrets.randbits(64))

        bg = self._draw_chart(w, h, scale, rng)
        mask, inner_shadow, piece_rim, slot_rim = self._create_jigsaw_assets(pw, ph, scale)

        origin = (target_x * scale, target_y * scale)
        cropped = bg.crop((*origin, origin[0] + pw * scale, origin[1] + ph * scale))
        cropped_bright = ImageEnhance.Brightness(cropped).enhance(1.18)
        cropped_bright = ImageEnhance.Contrast(cropped_bright).enhance(1.08)

        piece = Image.new("RGBA", cropped.size, (0, 0, 0, 0))
        piece.paste(cropped_bright, (0, 0), mask)

        rim_rgba = Image.new("RGBA", mask.size, (242, 248, 255, 225))
        rim_rgba.putalpha(piece_rim.point(lambda v: round(v * 0.85)))
        piece.alpha_composite(rim_rgba)

        bg_rgba = bg.convert("RGBA")
        slot_fill = Image.new("RGBA", mask.size, (6, 13, 22, 188))
        slot_fill.putalpha(mask.point(lambda v: round(v * 0.74)))
        bg_rgba.alpha_composite(slot_fill, origin)

        recess_shadow = Image.new("RGBA", mask.size, (2, 5, 10, 170))
        recess_shadow.putalpha(inner_shadow.point(lambda v: round(v * 0.65)))
        bg_rgba.alpha_composite(recess_shadow, origin)

        slot_outline = Image.new("RGBA", mask.size, (226, 238, 248, 135))
        slot_outline.putalpha(slot_rim.point(lambda v: round(v * 0.55)))
        bg_rgba.alpha_composite(slot_outline, origin)

        final_bg = bg_rgba.convert("RGB")

        bg_buf = io.BytesIO()
        final_bg.save(bg_buf, format="JPEG", quality=92, subsampling=0)
        bg_b64 = "data:image/jpeg;base64," + base64.b64encode(bg_buf.getvalue()).decode("ascii")

        piece_buf = io.BytesIO()
        piece.save(piece_buf, format="PNG")
        piece_b64 = "data:image/png;base64," + base64.b64encode(piece_buf.getvalue()).decode("ascii")

        challenge_id = f"cap_{secrets.token_hex(16)}"
        with self._lock:
            self._prune(now)
            self._challenges[challenge_id] = {
                "target_x": target_x,
                "target_y": target_y,
                "client_ip": client_ip,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
            }

        return CaptchaChallenge(
            challenge_id=challenge_id,
            bg_image=bg_b64,
            piece_image=piece_b64,
            y=target_y,
            width=w,
            height=h,
            piece_width=pw,
            piece_height=ph,
        )

    def verify_challenge(
        self,
        *,
        challenge_id: str,
        x: int,
        duration_ms: int = 0,
        trail: list[Any] | None = None,
        client_ip: str = "",
    ) -> tuple[bool, str | None, str | None]:
        """Verify the user's slide submission.

        Returns (verified: bool, captcha_token: str | None, error: str | None).
        """
        now = time.time()
        with self._lock:
            self._prune(now)
            entry = self._challenges.pop(challenge_id, None)

        if entry is None or entry["expires_at"] <= now:
            return False, None, "challenge_expired"

        target_x = entry["target_x"]
        # Position tolerance: ±6px
        if abs(x - target_x) > 6:
            return False, None, "position_mismatch"

        # Anti-bot velocity check: human dragging takes at least 200ms
        if duration_ms < 200:
            return False, None, "slide_too_fast"

        # Trajectory check if trail points supplied: bots typically emit 0 or 1 point
        if trail is not None and len(trail) > 0 and len(trail) < 3:
            return False, None, "trajectory_invalid"

        captcha_token = f"ctk_{secrets.token_hex(20)}"
        with self._lock:
            self._tokens[captcha_token] = {
                "client_ip": client_ip,
                "expires_at": now + self.token_ttl_seconds,
            }

        return True, captcha_token, None

    def validate_token(self, token: str, *, client_ip: str = "") -> bool:
        """Validate and consume a single-use captcha token."""
        if not token:
            return False
        now = time.time()
        with self._lock:
            self._prune(now)
            entry = self._tokens.get(token)
            if entry is None or entry["expires_at"] <= now:
                return False
            if entry["client_ip"] and client_ip and entry["client_ip"] != client_ip:
                return False
            self._tokens.pop(token, None)
            return True


GLOBAL_CAPTCHA = CaptchaService()
