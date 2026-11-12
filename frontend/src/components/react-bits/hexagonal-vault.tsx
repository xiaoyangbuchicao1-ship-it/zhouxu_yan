// React Bits Pro — Hexagonal Vault. Licensed source supplied by the project owner.
// https://framesbase.app/reactbits-pro/backgrounds/hexagonal-vault
"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type MutableRefObject,
  type ReactNode,
} from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { cn } from "@/lib/utils";

export interface HexagonalVaultProps {
  colors?: string[];
  backgroundColor?: string;
  cellSize?: number;
  lineWidth?: number;
  litWidth?: number;
  lattice?: number;
  glow?: number;
  glowRadius?: number;
  core?: number;
  vignette?: number;
  grain?: number;
  trailLength?: number;
  trailWidth?: number;
  follow?: number;
  flow?: number;
  autoplay?: boolean;
  shimmer?: number;
  speed?: number;
  interactive?: boolean;
  clickPulses?: boolean;
  pulseSpeed?: number;
  seed?: number;
  paused?: boolean;
  dpr?: number;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
  /** Observe the window for decorative, pointer-transparent page backgrounds. */
  eventSource?: "local" | "window";
}

const DEFAULT_COLORS = ["#5227FF", "#FF9FFC"];
const BASE_CELL = 72;
const MAX_PULSES = 4;

const passVertexShader = `
void main() {
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

const velocityShader = `
precision highp float;

uniform sampler2D uVelocity;
uniform vec2 uSize;
uniform vec2 uCanvas;
uniform float uDt;
uniform float uDecay;
uniform float uBlend;
uniform vec2 uFrom;
uniform vec2 uTo;
uniform vec2 uBrushVelocity;
uniform float uRadius;

float segment(vec2 p, vec2 a, vec2 b) {
  vec2 e = b - a;
  float t = clamp(dot(p - a, e) / max(dot(e, e), 1e-4), 0.0, 1.0);
  return length(p - a - e * t);
}

void main() {
  vec2 uv = gl_FragCoord.xy / uSize;
  vec2 p = uv * uCanvas - uCanvas * 0.5;
  vec2 v = texture2D(uVelocity, uv).xy;
  vec2 carried = texture2D(uVelocity, uv - v * uDt / uCanvas).xy * uDecay;
  float d = segment(p, uFrom, uTo) / uRadius;
  float w = exp(-d * d * d * d) * uBlend;
  gl_FragColor = vec4(mix(carried, uBrushVelocity, w), 0.0, 1.0);
}
`;

const lightShader = `
precision highp float;

uniform sampler2D uLight;
uniform sampler2D uVelocity;
uniform vec2 uSize;
uniform vec2 uCanvas;
uniform float uDt;
uniform float uDrift;
uniform vec2 uDecay;
uniform vec2 uFrom;
uniform vec2 uTo;
uniform float uRadius;
uniform float uEnergy;

float segment(vec2 p, vec2 a, vec2 b) {
  vec2 e = b - a;
  float t = clamp(dot(p - a, e) / max(dot(e, e), 1e-4), 0.0, 1.0);
  return length(p - a - e * t);
}

void main() {
  vec2 uv = gl_FragCoord.xy / uSize;
  vec2 p = uv * uCanvas - uCanvas * 0.5;
  vec2 v = texture2D(uVelocity, uv).xy;
  vec2 light = texture2D(uLight, uv - v * uDrift * uDt / uCanvas).xy * uDecay;
  float d = segment(p, uFrom, uTo) / uRadius;
  float paint = exp(-d * d) * uEnergy;
  gl_FragColor = vec4(max(light, vec2(paint)), 0.0, 1.0);
}
`;

const fragmentShader = `
precision highp float;

#define MAX_PULSES ${MAX_PULSES}

uniform vec2 uResolution;
uniform float uCell;
uniform float uTime;
uniform float uClock;
uniform sampler2D uLight;
uniform vec3 uLens;
uniform float uLensRadius;
uniform vec4 uPulses[MAX_PULSES];
uniform float uPulseSpeed;
uniform float uLineWidth;
uniform float uLitWidth;
uniform float uLattice;
uniform float uGlow;
uniform float uGlowRadius;
uniform float uShimmer;
uniform float uVignette;
uniform float uGrain;
uniform float uCore;
uniform vec3 uColorA;
uniform vec3 uColorB;
uniform vec3 uBackground;
uniform float uInk;
uniform float uTransparent;

const float HALF_EDGE = 0.2886751;

vec3 toSrgb(vec3 c) {
  c = clamp(c, 0.0, 1.0);
  return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));
}

float hash(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * 0.1031);
  q += dot(q, q.yzx + 33.33);
  return fract((q.x + q.y) * q.z);
}

void main() {
  vec2 p = gl_FragCoord.xy - uResolution * 0.5;
  vec2 q = p.yx / uCell;
  vec2 s = vec2(1.0, 1.7320508);
  vec4 hc = floor(vec4(q, q - vec2(0.5, 1.0)) / s.xyxy) + 0.5;
  vec4 h = vec4(q - hc.xy * s, q - (hc.zw + 0.5) * s);
  vec2 o;
  vec2 c;
  if (dot(h.xy, h.xy) < dot(h.zw, h.zw)) {
    o = h.xy;
    c = hc.xy * s;
  } else {
    o = h.zw;
    c = (hc.zw + 0.5) * s;
  }

  vec2 dirs[3];
  dirs[0] = vec2(1.0, 0.0);
  dirs[1] = vec2(0.5, 0.8660254);
  dirs[2] = vec2(-0.5, 0.8660254);

  float nearest = 1e9;
  vec2 nearestMid = vec2(0.0);
  float lineLit = 0.0;
  float glowLit = 0.0;
  float haloLit = 0.0;
  float heatSum = 0.0;
  float heatWeight = 1e-4;
  float pulseLine = 0.0;
  float pulseGlow = 0.0;
  float pulseHeat = 0.0;
  float aa = 0.8;
  float glowRadius = max(uGlowRadius, 0.5);

  for (int e = 0; e < 6; e++) {
    int slot = e < 3 ? e : e - 3;
    vec2 n = dirs[slot] * (e < 3 ? 1.0 : -1.0);
    vec2 t = vec2(-n.y, n.x);
    vec2 rel = o - n * 0.5;
    float along = dot(rel, t);
    float across = dot(rel, n);
    float dist = length(vec2(across, max(abs(along) - HALF_EDGE, 0.0))) * uCell;
    vec2 midPx = (c + n * 0.5).yx * uCell;
    if (dist < nearest) {
      nearest = dist;
      nearestMid = midPx;
    }

    vec2 tPx = t.yx;
    vec2 endA = midPx - tPx * HALF_EDGE * uCell;
    vec2 endB = midPx + tPx * HALF_EDGE * uCell;
    float clampedAlong = clamp(along, -HALF_EDGE, HALF_EDGE) * uCell;
    float fall = exp(-dist / glowRadius);

    vec2 at = midPx + tPx * clampedAlong;
    vec2 wake = texture2D(uLight, at / uResolution + 0.5).rg;
    vec2 offset = (at - uLens.xy) / uLensRadius;
    float r2 = dot(offset, offset);
    float lens = (0.62 * exp(-r2 * 4.0) + 0.38 * exp(-r2 * 0.8)) * uLens.z;
    float lit = max(wake.x, lens);
    float warm = max(wake.y, lens) * smoothstep(0.0, 0.3, lit);
    lit = pow(lit, 1.4);

    float edgeWidth = mix(uLineWidth, uLitWidth, clamp(lit, 0.0, 1.0));
    float edgeCover = 1.0 - smoothstep(edgeWidth * 0.5 - aa, edgeWidth * 0.5 + aa, dist);
    float edgeLine = edgeCover * lit;
    float edgeGlow = fall * lit;
    lineLit = max(lineLit, edgeLine);
    glowLit = max(glowLit, edgeGlow);
    haloLit = max(haloLit, exp(-dist / (glowRadius * 1.8)) * lit * warm * warm);
    heatSum += warm * (edgeLine + edgeGlow);
    heatWeight += edgeLine + edgeGlow;

    float spark = 0.0;
    float head = 0.0;
    for (int k = 0; k < MAX_PULSES; k++) {
      vec4 pl = uPulses[k];
      float age = uClock - pl.z;
      if (pl.w <= 0.0 || age < 0.0 || age > 7.0) continue;
      float da = length(endA - pl.xy);
      float db = length(endB - pl.xy);
      float fromNear = da < db ? clampedAlong + HALF_EDGE * uCell : HALF_EDGE * uCell - clampedAlong;
      float jitter = (hash(floor(midPx * 0.25) + pl.z) - 0.5) * 0.3 * uCell;
      float front = age * uPulseSpeed - min(da, db) + jitter;
      float gap = fromNear - front;
      float fade = exp(-age * 0.55) * pl.w;
      float headK = exp(-gap * gap / (0.018 * uCell * uCell)) * fade;
      float tailK = (gap < 0.0 ? exp(gap / (0.7 * uCell)) : 0.0) * fade;
      spark += headK + tailK * 0.3;
      head = max(head, headK);
    }
    float pulseWidth = mix(uLineWidth, uLitWidth, clamp(spark, 0.0, 1.0));
    float pulseCover = 1.0 - smoothstep(pulseWidth * 0.5 - aa, pulseWidth * 0.5 + aa, dist);
    pulseLine = max(pulseLine, pulseCover * spark);
    pulseGlow = max(pulseGlow, fall * spark);
    pulseHeat = max(pulseHeat, head * max(pulseCover, fall));
  }

  float heat = heatSum / heatWeight;

  float baseLine = 1.0 - smoothstep(uLineWidth * 0.5 - aa, uLineWidth * 0.5 + aa, nearest);
  float sweep = 0.5 + 0.5 * sin(dot(nearestMid / uCell, vec2(0.21, 0.13)) - uTime * 0.35);
  float shimmer = pow(sweep, 8.0) * uShimmer;

  float energy = clamp(lineLit + pulseLine, 0.0, 1.6);
  float glow = clamp(glowLit + pulseGlow, 0.0, 1.6);
  float hot = clamp(heat * 0.9 + pulseHeat, 0.0, 1.0);
  vec3 cool = mix(uColorA, vec3(dot(uColorA, vec3(0.2126, 0.7152, 0.0722))) * 1.6, 0.2);
  vec3 lineColor = mix(cool, uColorB, smoothstep(0.2, 0.75, hot));
  float radial = length(p / uResolution.y);

  float dither = (fract(52.9829189 * fract(dot(gl_FragCoord.xy, vec2(0.06711056, 0.00583715)))) - 0.5) / 255.0;
  float grain = (hash(gl_FragCoord.xy + fract(uTime) * 91.0) - 0.5) * uGrain * 0.05;

  if (uInk > 0.5) {
    vec3 ink = mix(uColorA, uColorB, smoothstep(0.2, 0.9, hot) * 0.6);
    vec3 neutral = vec3(0.08, 0.08, 0.1);
    vec3 color = uBackground * (1.0 - uVignette * 0.04 * smoothstep(0.2, 1.1, radial));
    color = mix(color, neutral, baseLine * uLattice * (0.22 + 0.12 * shimmer));
    color = mix(color, ink, clamp(glow * uGlow * 0.22 + haloLit * uGlow * 0.1, 0.0, 0.45));
    color = mix(color, ink, clamp(min(energy, 1.0) * 1.8, 0.0, 1.0));
    gl_FragColor = vec4(toSrgb(color) + dither + grain, 1.0);
    return;
  }

  vec3 latticeColor = mix(vec3(0.62, 0.62, 0.7), uColorA, 0.25);
  vec3 emission = latticeColor * baseLine * uLattice * (0.055 + 0.14 * shimmer);
  float white = smoothstep(0.55, 1.0, hot * min(energy, 1.0));
  vec3 core = mix(lineColor, vec3(1.0), uCore * white);
  emission += core * energy * (1.3 + 1.1 * white);
  emission += lineColor * glow * uGlow * 0.6;
  emission += mix(lineColor, uColorB, 0.5) * haloLit * uGlow * 0.12;

  if (uTransparent > 0.5) {
    vec3 light = 1.0 - exp(-emission * 1.05);
    float alpha = clamp(max(max(light.r, light.g), light.b), 0.0, 1.0);
    gl_FragColor = vec4(toSrgb(light) + (dither + grain) * step(0.004, alpha), alpha);
    return;
  }

  vec3 base = uBackground * (1.0 + uVignette * 0.6 * (1.0 - smoothstep(0.0, 1.1, radial)));
  vec3 color = base + emission;
  color = 1.0 - exp(-color * 1.05);
  gl_FragColor = vec4(toSrgb(color) + dither + grain, 1.0);
}
`;

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const isTransparent = (value: string) => {
  const normalized = value.trim().toLowerCase();
  if (normalized === "transparent") return true;
  if (/^#[0-9a-f]{8}$/.test(normalized)) return normalized.endsWith("00");
  if (/^#[0-9a-f]{4}$/.test(normalized)) return normalized.endsWith("0");
  const alpha = normalized.match(
    /^(?:rgba?|hsla?)\(.*[,/]\s*(0|0?\.0+|0%)\s*\)$/,
  );
  return alpha !== null;
};

const readColor = (target: THREE.Color, value: string, fallback: string) => {
  const trimmed = value.trim();
  try {
    target.set(/^#[0-9a-f]{8}$/i.test(trimmed) ? trimmed.slice(0, 7) : trimmed);
  } catch {
    target.set(fallback);
  }
  return target;
};

const random = (seed: number) => {
  let state = (Math.floor(seed * 7919) ^ 0x2f6b9d11) >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};

const subscribeToDpr = (notify: () => void) => {
  const media = window.matchMedia("(min-resolution: 2dppx)");
  media.addEventListener("change", notify);
  window.addEventListener("resize", notify);
  return () => {
    media.removeEventListener("change", notify);
    window.removeEventListener("resize", notify);
  };
};

const readDpr = () =>
  typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;

const subscribeToMotion = (notify: () => void) => {
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  media.addEventListener("change", notify);
  return () => media.removeEventListener("change", notify);
};

const readMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

interface PointerInput {
  x: number;
  y: number;
  inside: boolean;
  presses: number;
  pressX: number;
  pressY: number;
}

interface VaultSettings {
  colors: string[];
  backgroundColor: string;
  cellSize: number;
  lineWidth: number;
  litWidth: number;
  lattice: number;
  glow: number;
  glowRadius: number;
  core: number;
  vignette: number;
  grain: number;
  trailLength: number;
  trailWidth: number;
  follow: number;
  flow: number;
  autoplay: boolean;
  shimmer: number;
  speed: number;
  interactive: boolean;
  clickPulses: boolean;
  pulseSpeed: number;
  seed: number;
  paused: boolean;
  reduced: boolean;
}

interface VaultSceneProps {
  settings: VaultSettings;
  pointerRef: MutableRefObject<PointerInput>;
  wakeRef: MutableRefObject<(() => void) | null>;
}

interface Motion {
  time: number;
  clock: number;
  brushX: number;
  brushY: number;
  brushVX: number;
  brushVY: number;
  hasBrush: boolean;
  tracking: boolean;
  pointerX: number;
  pointerY: number;
  pointerVX: number;
  pointerVY: number;
  roaming: boolean;
  roamX: number;
  roamY: number;
  heading: number;
  roamClock: number;
  roamIdle: number;
  roamDelay: number;
  phases: number[];
  energy: number;
  presence: number;
  lastPaint: number;
  seed: number;
  presses: number;
  nextPulse: number;
  pulses: THREE.Vector4[];
}

interface FieldRig {
  light: THREE.WebGLRenderTarget[];
  velocity: THREE.WebGLRenderTarget[];
  flip: number;
  scene: THREE.Scene;
  camera: THREE.OrthographicCamera;
  lightMaterial: THREE.ShaderMaterial;
  velocityMaterial: THREE.ShaderMaterial;
  quad: THREE.Mesh;
  width: number;
  height: number;
}

const createTarget = (width: number, height: number) =>
  new THREE.WebGLRenderTarget(width, height, {
    type: THREE.HalfFloatType,
    format: THREE.RGBAFormat,
    minFilter: THREE.LinearFilter,
    magFilter: THREE.LinearFilter,
    depthBuffer: false,
    stencilBuffer: false,
  });

const createPass = (
  fragment: string,
  uniforms: Record<string, THREE.IUniform>,
) =>
  new THREE.ShaderMaterial({
    vertexShader: passVertexShader,
    fragmentShader: fragment,
    uniforms,
    depthTest: false,
    depthWrite: false,
  });

const createRig = (width: number, height: number): FieldRig => {
  const brush = () => ({
    uSize: { value: new THREE.Vector2(width, height) },
    uCanvas: { value: new THREE.Vector2(1, 1) },
    uDt: { value: 0 },
    uFrom: { value: new THREE.Vector2() },
    uTo: { value: new THREE.Vector2() },
    uRadius: { value: 100 },
  });
  const lightMaterial = createPass(lightShader, {
    ...brush(),
    uLight: { value: null },
    uVelocity: { value: null },
    uDrift: { value: 0.5 },
    uDecay: { value: new THREE.Vector2(1, 1) },
    uEnergy: { value: 0 },
  });
  const velocityMaterial = createPass(velocityShader, {
    ...brush(),
    uVelocity: { value: null },
    uDecay: { value: 1 },
    uBlend: { value: 0 },
    uBrushVelocity: { value: new THREE.Vector2() },
  });
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), lightMaterial);
  quad.frustumCulled = false;
  scene.add(quad);
  return {
    light: [createTarget(width, height), createTarget(width, height)],
    velocity: [createTarget(width, height), createTarget(width, height)],
    flip: 0,
    scene,
    camera,
    lightMaterial,
    velocityMaterial,
    quad,
    width,
    height,
  };
};

const disposeRig = (rig: FieldRig) => {
  rig.light.forEach((target) => target.dispose());
  rig.velocity.forEach((target) => target.dispose());
  rig.lightMaterial.dispose();
  rig.velocityMaterial.dispose();
  rig.quad.geometry.dispose();
};

const createUniforms = () => ({
  uResolution: { value: new THREE.Vector2(1, 1) },
  uCell: { value: BASE_CELL },
  uTime: { value: 0 },
  uClock: { value: 0 },
  uLight: { value: null as THREE.Texture | null },
  uLens: { value: new THREE.Vector3() },
  uLensRadius: { value: 100 },
  uPulses: {
    value: Array.from(
      { length: MAX_PULSES },
      () => new THREE.Vector4(0, 0, -99, 0),
    ),
  },
  uPulseSpeed: { value: 400 },
  uLineWidth: { value: 1 },
  uLitWidth: { value: 1.8 },
  uLattice: { value: 0.4 },
  uGlow: { value: 1.5 },
  uGlowRadius: { value: 4.5 },
  uShimmer: { value: 1 },
  uVignette: { value: 0 },
  uGrain: { value: 1 },
  uCore: { value: 0.4 },
  uColorA: { value: new THREE.Color() },
  uColorB: { value: new THREE.Color() },
  uBackground: { value: new THREE.Color() },
  uInk: { value: 0 },
  uTransparent: { value: 0 },
});

type VaultUniforms = ReturnType<typeof createUniforms>;

const VaultScene = ({ settings, pointerRef, wakeRef }: VaultSceneProps) => {
  const { gl, size, viewport, invalidate } = useThree();
  const materialRef = useRef<THREE.ShaderMaterial>(null);
  const rigRef = useRef<FieldRig | null>(null);
  const motionRef = useRef<Motion | null>(null);
  const uniforms = useMemo(() => createUniforms(), []);
  const colorKey = settings.colors.join("|");
  const { backgroundColor } = settings;
  const cellDevice = BASE_CELL * clamp(settings.cellSize, 0.5, 4) * viewport.dpr;
  const texel = clamp(Math.round(cellDevice / 20), 3, 10);
  const fieldWidth = Math.max(8, Math.ceil((size.width * viewport.dpr) / texel));
  const fieldHeight = Math.max(8, Math.ceil((size.height * viewport.dpr) / texel));

  useEffect(() => {
    const rig = createRig(fieldWidth, fieldHeight);
    rigRef.current = rig;
    invalidate();
    return () => {
      rigRef.current = null;
      disposeRig(rig);
    };
  }, [fieldWidth, fieldHeight, invalidate]);

  useEffect(() => {
    wakeRef.current = invalidate;
    return () => {
      wakeRef.current = null;
    };
  }, [invalidate, wakeRef]);

  useEffect(() => {
    const material = materialRef.current;
    if (!material) return;
    const values = material.uniforms as unknown as VaultUniforms;
    const palette = colorKey ? colorKey.split("|") : DEFAULT_COLORS;
    readColor(values.uColorA.value, palette[0] ?? DEFAULT_COLORS[0], DEFAULT_COLORS[0]);
    readColor(
      values.uColorB.value,
      palette[1] ?? palette[0] ?? DEFAULT_COLORS[1],
      DEFAULT_COLORS[1],
    );
    const transparent = isTransparent(backgroundColor);
    readColor(
      values.uBackground.value,
      transparent ? "#000000" : backgroundColor,
      "#000000",
    );
    const background = values.uBackground.value;
    const luminance =
      background.r * 0.2126 + background.g * 0.7152 + background.b * 0.0722;
    values.uInk.value = !transparent && luminance > 0.2 ? 1 : 0;
    values.uTransparent.value = transparent ? 1 : 0;
    invalidate();
  }, [colorKey, backgroundColor, invalidate]);

  useEffect(() => {
    invalidate();
  }, [settings, size, invalidate]);

  useFrame((state, delta) => {
    const material = materialRef.current;
    const rig = rigRef.current;
    if (!material || !rig) return;
    const values = material.uniforms as unknown as VaultUniforms;
    const s = settings;
    const input = pointerRef.current;
    const dt = Math.min(Math.max(delta, 0), 0.1);
    const animate = !s.paused && !s.reduced;
    const dpr = state.viewport.dpr;
    const cell = BASE_CELL * clamp(s.cellSize, 0.5, 4) * dpr;
    const width = size.width * dpr;
    const height = size.height * dpr;
    const pace = clamp(s.speed, 0, 4);

    if (!motionRef.current || motionRef.current.seed !== s.seed) {
      const next = random(s.seed + 0.5);
      motionRef.current = {
        time: 0,
        clock: 0,
        brushX: 0,
        brushY: 0,
        brushVX: 0,
        brushVY: 0,
        hasBrush: false,
        tracking: false,
        pointerX: 0,
        pointerY: 0,
        pointerVX: 0,
        pointerVY: 0,
        roaming: false,
        roamX: (next() - 0.5) * 0.5 * width,
        roamY: (next() - 0.5) * 0.4 * height,
        heading: next() * Math.PI * 2,
        roamClock: next() * 40,
        roamIdle: 0,
        roamDelay: 0,
        phases: [0, 1, 2, 3].map(() => next() * Math.PI * 2),
        energy: 0,
        presence: 0,
        lastPaint: -99,
        seed: s.seed,
        presses: input.presses,
        nextPulse: 0,
        pulses: Array.from(
          { length: MAX_PULSES },
          () => new THREE.Vector4(0, 0, -99, 0),
        ),
      };
    }
    const motion = motionRef.current;

    if (animate) motion.time += dt * pace;
    if (!s.reduced) motion.clock += dt;

    const tracking = s.interactive && !s.reduced && input.inside;
    const roaming = s.autoplay && !tracking;
    const pointerX = (input.x - size.width / 2) * dpr;
    const pointerY = (size.height / 2 - input.y) * dpr;

    if (roaming && !motion.roaming) {
      if (motion.hasBrush) {
        motion.roamX = clamp(
          motion.brushX + motion.brushVX * 0.2,
          -width * 0.5 + cell,
          width * 0.5 - cell,
        );
        motion.roamY = clamp(
          motion.brushY + motion.brushVY * 0.2,
          -height * 0.5 + cell,
          height * 0.5 - cell,
        );
        if (Math.hypot(motion.brushVX, motion.brushVY) > cell * 0.2) {
          motion.heading = Math.atan2(motion.brushVY, motion.brushVX);
        }
        motion.roamDelay = 0.8;
      } else {
        motion.brushX = motion.roamX;
        motion.brushY = motion.roamY;
        motion.brushVX = 0;
        motion.brushVY = 0;
        motion.hasBrush = true;
        motion.roamDelay = 0;
      }
      motion.roamIdle = 0;
    }
    motion.roaming = roaming;

    let cruise = 0;
    if (roaming && animate) {
      motion.roamIdle += dt;
      const ramp = clamp((motion.roamIdle - motion.roamDelay) / 1.6, 0, 1);
      const eased = ramp * ramp * (3 - 2 * ramp);
      motion.roamClock += dt * pace * eased;
      const t = motion.roamClock;
      const [a, b, c, d] = motion.phases;
      let turn =
        0.45 * Math.sin(t * 0.43 + a) +
        0.3 * Math.sin(t * 0.27 + b) +
        0.18 * Math.sin(t * 0.71 + c);
      const reachX = Math.max(width * 0.5 - cell * 1.5, cell);
      const reachY = Math.max(height * 0.5 - cell * 1.2, cell);
      const outside = Math.hypot(motion.roamX / reachX, motion.roamY / reachY) - 0.7;
      if (outside > 0) {
        const home = Math.atan2(-motion.roamY, -motion.roamX);
        const miss = Math.atan2(
          Math.sin(home - motion.heading),
          Math.cos(home - motion.heading),
        );
        turn += miss * Math.min(outside * 6, 3);
      }
      motion.heading += turn * dt * pace * eased;
      cruise = cell * 1.1 * (0.8 + 0.2 * Math.sin(t * 0.19 + d)) * pace * eased;
      motion.roamX += Math.cos(motion.heading) * cruise * dt;
      motion.roamY += Math.sin(motion.heading) * cruise * dt;
    }

    if (
      tracking &&
      (!motion.hasBrush || (!motion.tracking && motion.presence < 0.05))
    ) {
      motion.brushX = pointerX;
      motion.brushY = pointerY;
      motion.brushVX = 0;
      motion.brushVY = 0;
      motion.hasBrush = true;
    }
    if (tracking && !motion.tracking) {
      motion.pointerX = pointerX;
      motion.pointerY = pointerY;
      motion.pointerVX = 0;
      motion.pointerVY = 0;
    }
    motion.tracking = tracking;
    if (tracking && dt > 0) {
      const smoothing = 1 - Math.exp(-dt / 0.06);
      motion.pointerVX += ((pointerX - motion.pointerX) / dt - motion.pointerVX) * smoothing;
      motion.pointerVY += ((pointerY - motion.pointerY) / dt - motion.pointerVY) * smoothing;
      motion.pointerX = pointerX;
      motion.pointerY = pointerY;
    } else {
      motion.pointerVX = 0;
      motion.pointerVY = 0;
    }

    const steering = tracking || roaming;
    const targetX = tracking ? pointerX : motion.roamX;
    const targetY = tracking ? pointerY : motion.roamY;
    const fromX = motion.brushX;
    const fromY = motion.brushY;
    if (s.reduced && roaming) {
      motion.brushX = targetX;
      motion.brushY = targetY;
      motion.brushVX = 0;
      motion.brushVY = 0;
    } else if (motion.hasBrush && dt > 0) {
      const omega = 10 * clamp(s.follow, 0.2, 4);
      const damping = 1.24 * omega;
      const steps = Math.max(4, Math.ceil(dt / 0.008));
      const h = dt / steps;
      for (let k = 0; k < steps; k++) {
        if (steering) {
          motion.brushVX +=
            ((targetX - motion.brushX) * omega * omega - motion.brushVX * damping) * h;
          motion.brushVY +=
            ((targetY - motion.brushY) * omega * omega - motion.brushVY * damping) * h;
        } else {
          motion.brushVX *= Math.exp(-h * 6);
          motion.brushVY *= Math.exp(-h * 6);
        }
        motion.brushX += motion.brushVX * h;
        motion.brushY += motion.brushVY * h;
      }
    }

    const brushSpeed = Math.hypot(motion.brushVX, motion.brushVY) / dpr;
    const pointerSpeed = Math.hypot(motion.pointerVX, motion.pointerVY) / dpr;
    const cruiseSpeed = cruise / dpr;
    const drive = tracking
      ? 1 - Math.exp(-pointerSpeed / 120)
      : roaming
        ? 0.85 * (1 - Math.exp(-cruiseSpeed / 120))
        : 0;
    if (s.reduced) {
      motion.energy = 0;
      motion.presence = roaming ? 1 : 0;
    } else {
      const response = drive > motion.energy ? 0.08 : 0.35;
      motion.energy += (drive - motion.energy) * (1 - Math.exp(-dt / response));
      motion.presence +=
        ((steering ? 1 : 0) - motion.presence) * (1 - Math.exp(-dt / 0.3));
    }
    if (motion.energy > 0.01) motion.lastPaint = motion.clock;
    if (!steering && motion.presence < 0.005 && motion.energy < 0.005 && brushSpeed < 1) {
      motion.hasBrush = false;
    }

    if (input.presses !== motion.presses) {
      motion.presses = input.presses;
      if (s.clickPulses && s.interactive && !s.reduced) {
        const slot = motion.nextPulse;
        motion.nextPulse = (slot + 1) % MAX_PULSES;
        motion.pulses[slot].set(
          (input.pressX - size.width / 2) * dpr,
          (size.height / 2 - input.pressY) * dpr,
          motion.clock,
          1,
        );
      }
    }

    const source = rig.flip;
    const sink = 1 - source;
    const radius = clamp(s.trailWidth, 0.2, 4) * 2 * cell;
    const speedFactor = 1 - Math.exp(-Math.max(pointerSpeed, cruiseSpeed) / 600);
    const wake = radius * (0.35 + 0.25 * speedFactor);
    const fade = clamp(s.trailLength, 0.1, 8) * 0.26;
    const painting = motion.hasBrush ? motion.energy : 0;
    const brushPass = (pass: Record<string, THREE.IUniform>) => {
      (pass.uCanvas.value as THREE.Vector2).set(width, height);
      (pass.uSize.value as THREE.Vector2).set(rig.width, rig.height);
      (pass.uFrom.value as THREE.Vector2).set(fromX, fromY);
      (pass.uTo.value as THREE.Vector2).set(motion.brushX, motion.brushY);
      pass.uDt.value = dt;
    };

    const velocity = rig.velocityMaterial.uniforms;
    brushPass(velocity);
    velocity.uRadius.value = radius;
    velocity.uVelocity.value = rig.velocity[source].texture;
    velocity.uDecay.value = Math.exp(-dt / 0.25);
    velocity.uBlend.value = (1 - Math.exp(-dt / 0.05)) * painting;
    (velocity.uBrushVelocity.value as THREE.Vector2).set(motion.brushVX, motion.brushVY);

    const light = rig.lightMaterial.uniforms;
    brushPass(light);
    light.uRadius.value = wake;
    light.uLight.value = rig.light[source].texture;
    light.uVelocity.value = rig.velocity[sink].texture;
    light.uDrift.value = clamp(s.flow, 0, 1) * 0.8;
    (light.uDecay.value as THREE.Vector2).set(
      Math.exp(-dt / fade),
      Math.exp(-dt / (fade * 0.5)),
    );
    light.uEnergy.value = painting * 0.7;

    const previousTarget = gl.getRenderTarget();
    rig.quad.material = rig.velocityMaterial;
    gl.setRenderTarget(rig.velocity[sink]);
    gl.render(rig.scene, rig.camera);
    rig.quad.material = rig.lightMaterial;
    gl.setRenderTarget(rig.light[sink]);
    gl.render(rig.scene, rig.camera);
    gl.setRenderTarget(previousTarget);
    rig.flip = sink;

    let pulsing = false;
    for (let k = 0; k < MAX_PULSES; k++) {
      values.uPulses.value[k].copy(motion.pulses[k]);
      if (motion.pulses[k].w > 0 && motion.clock - motion.pulses[k].z < 7) pulsing = true;
    }

    values.uLight.value = rig.light[sink].texture;
    values.uLens.value.set(
      motion.brushX,
      motion.brushY,
      motion.hasBrush ? motion.presence * (0.45 + 0.55 * motion.energy) : 0,
    );
    values.uLensRadius.value = radius;
    values.uResolution.value.set(width, height);
    values.uCell.value = cell;
    values.uTime.value = motion.time;
    values.uClock.value = motion.clock;
    values.uPulseSpeed.value = clamp(s.pulseSpeed, 0.1, 5) * 5.5 * cell;
    values.uLineWidth.value = clamp(s.lineWidth, 0.2, 4) * dpr;
    values.uLitWidth.value = clamp(s.litWidth, 0.5, 8) * dpr;
    values.uLattice.value = clamp(s.lattice, 0, 3);
    values.uGlow.value = clamp(s.glow, 0, 3);
    values.uGlowRadius.value = clamp(s.glowRadius, 0.1, 4) * 4.5 * dpr;
    values.uShimmer.value = clamp(s.shimmer, 0, 2);
    values.uVignette.value = clamp(s.vignette, 0, 1);
    values.uGrain.value = clamp(s.grain, 0, 1);
    values.uCore.value = clamp(s.core, 0, 1);

    const settling =
      Math.abs((steering ? 1 : 0) - motion.presence) > 0.002 ||
      motion.energy > 0.002 ||
      brushSpeed > 0.5;
    const active =
      pulsing || settling || motion.clock - motion.lastPaint < fade * 6;
    if (!animate && !s.reduced && active) invalidate();
  });

  return (
    <mesh frustumCulled={false}>
      <planeGeometry args={[2, 2]} />
      <shaderMaterial
        ref={materialRef}
        vertexShader={passVertexShader}
        fragmentShader={fragmentShader}
        uniforms={uniforms}
        depthTest={false}
        depthWrite={false}
      />
    </mesh>
  );
};

export const HexagonalVault = ({
  colors = DEFAULT_COLORS,
  backgroundColor = "#0a0a0a",
  cellSize = 1.9,
  lineWidth = 0.5,
  litWidth = 1.8,
  lattice = 0.4,
  glow = 1.5,
  glowRadius = 0.5,
  core = 0.4,
  vignette = 0,
  grain = 1,
  trailLength = 3,
  trailWidth = 1,
  follow = 0.5,
  flow = 0.5,
  autoplay = true,
  shimmer = 1,
  speed = 1,
  interactive = true,
  clickPulses = true,
  pulseSpeed = 1,
  seed = 0,
  paused = false,
  dpr = 2,
  className,
  style,
  children,
  eventSource = "local",
}: HexagonalVaultProps) => {
  const rootRef = useRef<HTMLDivElement>(null);
  const pointerRef = useRef<PointerInput>({
    x: 0,
    y: 0,
    inside: false,
    presses: 0,
    pressX: 0,
    pressY: 0,
  });
  const wakeRef = useRef<(() => void) | null>(null);
  const [visible, setVisible] = useState(true);
  const [pageVisible, setPageVisible] = useState(() => !document.hidden);

  useEffect(() => {
    const update = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  const reducedMotion = useSyncExternalStore(
    subscribeToMotion,
    readMotion,
    () => false,
  );
  const deviceDpr = useSyncExternalStore(subscribeToDpr, readDpr, () => 1);
  const pixelRatio = Math.min(deviceDpr, Math.max(dpr, 0.5));
  const animated = !paused && !reducedMotion;
  const transparent = isTransparent(backgroundColor);

  const settings: VaultSettings = {
    colors,
    backgroundColor,
    cellSize,
    lineWidth,
    litWidth,
    lattice,
    glow,
    glowRadius,
    core,
    vignette,
    grain,
    trailLength,
    trailWidth,
    follow,
    flow,
    autoplay,
    shimmer,
    speed,
    interactive,
    clickPulses,
    pulseSpeed,
    seed,
    paused,
    reduced: reducedMotion,
  };

  useEffect(() => {
    const node = rootRef.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { rootMargin: "80px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const node = rootRef.current;
    if (!node) return;
    const locate = (event: PointerEvent) => {
      const box = node.getBoundingClientRect();
      const state = pointerRef.current;
      state.x = event.clientX - box.left;
      state.y = event.clientY - box.top;
      state.inside = state.x >= 0 && state.x <= box.width && state.y >= 0 && state.y <= box.height;
    };
    const move = (event: PointerEvent) => {
      locate(event);
      wakeRef.current?.();
    };
    const down = (event: PointerEvent) => {
      locate(event);
      const state = pointerRef.current;
      state.pressX = state.x;
      state.pressY = state.y;
      state.presses += 1;
      wakeRef.current?.();
    };
    const leave = () => {
      pointerRef.current.inside = false;
      wakeRef.current?.();
    };
    const target = eventSource === "window" ? window : node;
    target.addEventListener("pointermove", move, { passive: true });
    target.addEventListener("pointerdown", down, { passive: true });
    target.addEventListener("pointerleave", leave, { passive: true });
    target.addEventListener("pointercancel", leave, { passive: true });
    window.addEventListener("blur", leave);
    return () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerdown", down);
      target.removeEventListener("pointerleave", leave);
      target.removeEventListener("pointercancel", leave);
      window.removeEventListener("blur", leave);
    };
  }, [eventSource]);

  return (
    <div
      ref={rootRef}
      className={cn("relative isolate overflow-hidden", className)}
      style={{
        backgroundColor: transparent ? undefined : backgroundColor,
        ...style,
      }}
    >
      <div className="absolute inset-0" aria-hidden="true">
        <Canvas
          dpr={pixelRatio}
          frameloop={visible && pageVisible && animated ? "always" : "demand"}
          fallback={<div className="auth-vault-fallback" />}
          gl={{
            antialias: false,
            alpha: true,
            premultipliedAlpha: true,
            powerPreference: "high-performance",
          }}
          style={{ pointerEvents: "none" }}
        >
          <VaultScene
            settings={settings}
            pointerRef={pointerRef}
            wakeRef={wakeRef}
          />
        </Canvas>
      </div>
      {children ? (
        <div className="relative z-10 h-full w-full">{children}</div>
      ) : null}
    </div>
  );
};

export default HexagonalVault;
