import { useEffect, useRef, useState } from "react";
import gsap from "gsap";
import Navbar from "./Navbar";

const VIDEO_SRC = "/media/zhouxu-bg.mp4";

const Hero = () => {
  const heroRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  // GSAP entrance
  useEffect(() => {
    const ctx = gsap.context(() => {
      const tl = gsap.timeline({ defaults: { ease: "power3.out" } });

      tl.fromTo(
        ".name-reveal",
        { opacity: 0, y: 50 },
        { opacity: 1, y: 0, duration: 1.2 },
        0.1
      );

      tl.fromTo(
        ".blur-in",
        { opacity: 0, filter: "blur(10px)", y: 20 },
        {
          opacity: 1,
          filter: "blur(0px)",
          y: 0,
          duration: 1,
          stagger: 0.1,
        },
        0.3
      );
    }, heroRef);

    return () => ctx.revert();
  }, []);

  return (
    <section id="home" ref={heroRef} className="relative min-h-[100svh] flex flex-col">
      <Navbar />

      {/* Background video */}
      <div className="absolute inset-0 z-0 overflow-hidden">
        <video
          ref={videoRef}
          src={VIDEO_SRC}
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          className="absolute top-1/2 left-1/2 min-w-full min-h-full -translate-x-1/2 -translate-y-1/2 object-cover"
        />
        <div className="absolute inset-0 bg-black/20" />
        <div className="absolute inset-x-0 bottom-0 h-48 bg-gradient-to-t from-bg to-transparent" />
      </div>

      {/* Content */}
      <div className="relative z-10 flex-1 flex flex-col items-center justify-center text-center px-6 pt-28 pb-36">
        <span className="blur-in text-xs text-muted uppercase tracking-[0.3em] mb-8">
          ZHOUXU · MARITIME INTELLIGENCE
        </span>

        <h1 lang="en" aria-label="舟序 Zhouxu" className="name-reveal text-[clamp(100px,13vw,180px)] font-display italic leading-[1.08] tracking-[-0.045em] text-text-primary mb-7 px-[0.13em]">
          Zhouxu
        </h1>

        <p className="blur-in font-editorial text-[22px] md:text-[28px] lg:text-[33px] leading-[1.7] tracking-[0.06em] text-text-primary mb-5">
          <span className="inline-block">让事实有序，</span><span className="inline-block">让判断有据。</span>
        </p>

        <p className="blur-in text-sm text-muted leading-[1.95] max-w-[480px] mb-10">
          面向海事争议与仲裁工作，连接法律研究、材料梳理与分析起草，让复杂问题有清晰的处理脉络。
        </p>

        {/* CTA Buttons */}
        <div className="blur-in flex items-center gap-4">
          <SeeWorksButton />
          <ReachOutButton />
        </div>
      </div>

      {/* Scroll indicator */}
      <div className="blur-in absolute bottom-8 left-1/2 -translate-x-1/2 z-10 flex flex-col items-center gap-2">
        <span className="text-xs text-muted uppercase tracking-[0.2em]">
          EXPLORE ↓
        </span>
        <div className="w-px h-10 bg-stroke relative overflow-hidden">
          <div className="w-full h-1/2 bg-text-primary animate-scroll-down" />
        </div>
      </div>
    </section>
  );
};

const SeeWorksButton = () => {
  const [hovered, setHovered] = useState(false);

  return (
    <a href="#work"
      className="relative rounded-full text-sm transition-transform duration-200 hover:scale-105"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <span
        className={`absolute rounded-full accent-gradient transition-opacity duration-300 ${
          hovered ? "opacity-100" : "opacity-0"
        }`}
        style={{ inset: "-2px" }}
      />
      <span
        className={`relative z-10 block px-7 py-3.5 rounded-full transition-colors duration-300 ${
          hovered ? "bg-bg text-text-primary" : "bg-text-primary text-bg"
        }`}
      >
        探索舟序
      </span>
    </a>
  );
};

const ReachOutButton = () => {
  const [hovered, setHovered] = useState(false);

  return (
    <a href="#workflow"
      className="relative rounded-full text-sm transition-transform duration-200 hover:scale-105"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <span
        className={`absolute rounded-full accent-gradient transition-opacity duration-300 ${
          hovered ? "opacity-100" : "opacity-0"
        }`}
        style={{ inset: "-2px" }}
      />
      <span
        className={`relative z-10 block px-7 py-3.5 rounded-full border-2 transition-colors duration-300 ${
          hovered
            ? "border-transparent bg-bg text-text-primary"
            : "border-stroke bg-bg text-text-primary"
        }`}
      >
        了解工作方式
      </span>
    </a>
  );
};

export default Hero;
