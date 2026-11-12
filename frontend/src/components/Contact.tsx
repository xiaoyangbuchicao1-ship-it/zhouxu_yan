import { useRef, useEffect } from "react";
import { motion } from "framer-motion";
import gsap from "gsap";

const VIDEO_SRC = "/media/zhouxu-bg.mp4";

const socials = [
  { name: "舟序 / ZHOUXU", href: "#home" },
  { name: "应用场景", href: "#work" },
  { name: "工作方式", href: "#workflow" },
  { name: "功能规划", href: "#roadmap" },
];

const ease = [0.25, 0.1, 0.25, 1] as [number, number, number, number];

const Contact = () => {
  const marqueeRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  // GSAP marquee
  useEffect(() => {
    const timeout = setTimeout(() => {
      if (!marqueeRef.current) return;
      const inner = marqueeRef.current.querySelector(".marquee-inner") as HTMLElement;
      if (!inner) return;
      const anim = gsap.to(inner, {
        xPercent: -50,
        duration: 40,
        ease: "none",
        repeat: -1,
      });
      return () => anim.kill();
    }, 100);
    return () => clearTimeout(timeout);
  }, []);

  return (
    <section id="contact" className="relative bg-bg pt-16 md:pt-20 pb-8 md:pb-12 overflow-hidden">
      {/* Background video */}
      <div className="absolute inset-0 w-full h-full z-0 overflow-hidden">
        <video
          ref={videoRef}
          src={VIDEO_SRC}
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          className="absolute top-1/2 left-1/2 min-w-full min-h-full -translate-x-1/2 -translate-y-1/2 object-cover scale-y-[-1]"
        />
        <div className="absolute inset-0 bg-black/60" />
        <div className="absolute top-0 left-0 right-0 h-32 bg-gradient-to-b from-bg to-transparent" />
        <div className="absolute bottom-0 left-0 right-0 h-24 bg-gradient-to-t from-bg to-transparent" />
      </div>

      {/* Content */}
      <div className="relative z-10">
        {/* Marquee */}
        <div className="overflow-hidden mb-12 md:mb-16">
          <div ref={marqueeRef}>
            <div className="marquee-inner flex whitespace-nowrap w-max" style={{ willChange: "transform" }}>
              {[...Array(10)].map((_, i) => (
                <span key={i} className="text-5xl md:text-7xl lg:text-8xl font-display italic text-text-primary/10 mx-4 md:mx-8">
                  CLARITY IN EVERY CASE
                  <span className="mx-4 md:mx-8">•</span>
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* CTA */}
        <div className="max-w-[1200px] mx-auto px-6 md:px-10 lg:px-16">
          <motion.div
            className="text-center mb-16 md:mb-20"
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            transition={{ duration: 1, ease }}
            viewport={{ once: true, margin: "-100px" }}
          >
            <p className="text-base md:text-lg text-muted mb-8 max-w-md mx-auto">
              从一份材料，到一个清楚的问题。让海事工作，更有条理。
            </p>
            <motion.a
              href="#roadmap"
              className="group relative inline-flex items-center gap-3 px-8 py-4 bg-bg border-2 border-stroke rounded-full"
              whileTap={{ scale: 0.97 }}
            >
              <span
                className="absolute inset-0 rounded-full p-[2px] bg-gradient-to-r from-[#89AACC] to-[#4E85BF] opacity-0 group-hover:opacity-100 transition-opacity duration-300"
                style={{ margin: "-2px" }}
              >
                <span className="block w-full h-full rounded-full bg-bg" />
              </span>
              <span className="relative z-10 text-lg text-text-primary break-all sm:break-normal">
                探索舟序的下一步
              </span>
              <svg
                className="relative z-10 w-[18px] h-[18px] text-muted group-hover:text-text-primary group-hover:translate-x-1 group-hover:-translate-y-1 transition-all duration-300"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path d="M7 17L17 7M17 7H7M17 7V17" />
              </svg>
            </motion.a>
          </motion.div>

          {/* Footer bar */}
          <div className="flex flex-col md:flex-row items-center justify-between gap-6 pt-8 border-t border-stroke">
            <div className="flex flex-wrap items-center justify-center md:justify-start gap-6 md:gap-8">
              {socials.map((s) => (
                <a
                  key={s.name}
                  href={s.href}
                  className="text-sm text-muted hover:text-text-primary transition-colors hover:-translate-y-0.5 duration-200"
                >
                  {s.name}
                </a>
              ))}
            </div>
            <div className="flex items-center gap-3">
              <span className="relative flex h-2 w-2">
                <span className="relative inline-flex rounded-full h-2 w-2 bg-[#89AACC]" />
              </span>
              <span className="text-xs text-muted">用于辅助分析，重要结论请由专业人员复核。</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default Contact;
