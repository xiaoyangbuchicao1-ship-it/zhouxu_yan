import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { useAuthSession } from "@/lib/auth-session";
import logo from "../../zhou-xu-mark.png";

const NAV_LINKS = [
  { label: "首页", href: "#home" },
  { label: "应用场景", href: "#work" },
  { label: "工作方式", href: "#workflow" },
];

const Navbar = () => {
  const session = useAuthSession();
  const [active, setActive] = useState("首页");
  const [scrolled, setScrolled] = useState(false);
  const [logoHovered, setLogoHovered] = useState(false);
  const [sayHiHovered, setSayHiHovered] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 100);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 flex justify-center pt-4 md:pt-6 px-4">
      <div
        className={`inline-flex items-center rounded-full backdrop-blur-md border border-white/10 bg-surface px-2 py-2 transition-shadow duration-300 ${
          scrolled ? "shadow-md shadow-black/10" : ""
        }`}
      >
        {/* Logo */}
        <a href="#home" aria-label="舟序，返回首页"
          className="relative w-9 h-9 shrink-0 rounded-full p-[2px] transition-transform duration-200"
          onMouseEnter={() => setLogoHovered(true)}
          onMouseLeave={() => setLogoHovered(false)}
          style={{
            background: logoHovered
              ? "linear-gradient(90deg, #4E85BF 0%, #89AACC 100%)"
              : "linear-gradient(90deg, #89AACC 0%, #4E85BF 100%)",
          }}
        >
          <span className="flex items-center justify-center w-full h-full rounded-full bg-white overflow-hidden">
            <img src={logo} alt="" aria-hidden="true"
              className={`w-full h-full object-contain transition-transform duration-200 ${
                logoHovered ? "scale-110" : ""
              }`}
            />
          </span>
        </a>

        {/* Divider */}
        <span className="hidden sm:block w-px h-5 bg-stroke mx-1" />

        {/* Nav links */}
        {NAV_LINKS.map((link) => (
          <a
            key={link.label}
            href={link.href}
            onClick={() => setActive(link.label)}
            className={`text-[11px] sm:text-sm whitespace-nowrap rounded-full px-2 sm:px-4 py-1.5 sm:py-2 transition-colors duration-200 ${
              active === link.label
                ? "text-text-primary bg-stroke/50"
                : "text-muted hover:text-text-primary hover:bg-stroke/50"
            }`}
          >
            {link.label}
          </a>
        ))}

        {/* Divider */}
        <span className="hidden sm:block w-px h-5 bg-stroke mx-1" />

        {/* Say hi button */}
        <Link to={session ? "/workspace" : "/login"}
          className="relative text-[11px] sm:text-sm whitespace-nowrap rounded-full px-2 sm:px-4 py-1.5 sm:py-2 text-muted hover:text-text-primary transition-colors duration-200"
          onMouseEnter={() => setSayHiHovered(true)}
          onMouseLeave={() => setSayHiHovered(false)}
        >
          {/* Gradient border on hover */}
          <span
            className={`absolute rounded-full transition-opacity duration-300 accent-gradient ${
              sayHiHovered ? "opacity-100" : "opacity-0"
            }`}
            style={{ inset: "-2px" }}
          />
          <span className="relative z-10 flex items-center gap-1 bg-surface rounded-full px-2 sm:px-4 py-1.5 sm:py-2 -mx-2 sm:-mx-4 -my-1.5 sm:-my-2 backdrop-blur-md">
            {session ? "工作台" : "登录"} <span className="text-[10px]">↗</span>
          </span>
        </Link>
      </div>
    </nav>
  );
};

export default Navbar;
