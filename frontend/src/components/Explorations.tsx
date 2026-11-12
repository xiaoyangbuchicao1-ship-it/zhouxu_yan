import { useRef, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

const explorations = [
  { id: 1, title: "文件记载", category: "FACTS · 视觉配图", alt: "航海图与材料夹的海事研究配图", image: "/explorations/planet.jpeg" },
  { id: 2, title: "合同约定", category: "TERMS · 视觉配图", alt: "提单文件与罗盘的合同审查配图", image: "/explorations/ascii.jpeg" },
  { id: 3, title: "时间脉络", category: "TIMELINE · 视觉配图", alt: "夜间海面航线的争议时间线配图", image: "/explorations/smoke.jpeg" },
  { id: 4, title: "各方主张", category: "POSITIONS · 视觉配图", alt: "相对而立的玻璃形体与海面倒影配图", image: "/explorations/cylinder.jpeg" },
  { id: 5, title: "计算前提", category: "ASSUMPTIONS · 视觉配图", alt: "海面涟漪与测量弧线的索赔计算配图", image: "/explorations/wave.jpeg" },
  { id: 6, title: "参考依据", category: "SOURCES · 视觉配图", alt: "档案文件与航海星标的法律依据配图", image: "/explorations/cubes.jpeg" },
];

const leftItems = explorations.filter((_, i) => i % 2 === 0);
const rightItems = explorations.filter((_, i) => i % 2 !== 0);

const ExplorationCard = ({
  item,
  onClick,
}: {
  item: (typeof explorations)[0];
  onClick: () => void;
}) => {
  const rotation = (item.id % 2 === 0 ? 1 : -1) * (1.5 + (item.id % 3));

  return (
    <button type="button" aria-label={`查看${item.title}的视觉配图`}
      className="group block text-left relative w-full aspect-square cursor-pointer"
      style={{ transform: `rotate(${rotation}deg)` }}
      onClick={onClick}
    >
      {/* Outer border frame */}
      <div className="absolute -inset-4 border border-stroke rounded-[40px] -z-10 group-hover:scale-105 transition-transform duration-700" />

      {/* Blue tint */}
      <div className="absolute inset-0 z-10 bg-blue-500/10 mix-blend-overlay rounded-3xl" />

      {/* Image */}
      <div className="relative h-full w-full rounded-3xl overflow-hidden shadow-2xl shadow-black/20 z-0">
        <img
          src={item.image}
          alt={item.alt}
          className="w-full h-full object-cover transition-transform duration-700 ease-out group-hover:scale-105"
        />
        {/* Halftone */}
        <div
          className="absolute inset-0 opacity-20 mix-blend-multiply"
          style={{
            backgroundImage: "radial-gradient(circle, #000 1px, transparent 1px)",
            backgroundSize: "4px 4px",
          }}
        />
        {/* Hover gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
      </div>

      {/* Hover content */}
      <div className="absolute bottom-0 left-0 right-0 p-6 translate-y-4 group-hover:translate-y-0 opacity-0 group-hover:opacity-100 transition-all duration-500 z-10">
        <span className="text-[10px] text-white/60 uppercase tracking-widest block mb-1">
          {item.category}
        </span>
        <span className="text-white text-lg font-medium">{item.title}</span>
      </div>
    </button>
  );
};

const Explorations = () => {
  const sectionRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const leftColRef = useRef<HTMLDivElement>(null);
  const rightColRef = useRef<HTMLDivElement>(null);
  const [lightbox, setLightbox] = useState<(typeof explorations)[0] | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const ctx = gsap.context(() => {
      // Pin center content
      ScrollTrigger.create({
        trigger: sectionRef.current,
        start: "top top",
        end: "bottom bottom",
        pin: contentRef.current,
        pinSpacing: false,
      });

      // Left column parallax
      gsap.fromTo(
        leftColRef.current,
        { y: "10vh" },
        {
          y: "-120vh",
          ease: "none",
          scrollTrigger: {
            trigger: sectionRef.current,
            start: "top bottom",
            end: "bottom top",
            scrub: 1,
          },
        }
      );

      // Right column parallax
      gsap.fromTo(
        rightColRef.current,
        { y: "40vh" },
        {
          y: "-100vh",
          ease: "none",
          scrollTrigger: {
            trigger: sectionRef.current,
            start: "top bottom",
            end: "bottom top",
            scrub: 1.5,
          },
        }
      );
    }, sectionRef);

    return () => ctx.revert();
  }, []);

  // Close lightbox on Escape
  useEffect(() => {
    if (!lightbox) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightbox(null);
      if (e.key === "Tab") { e.preventDefault(); closeRef.current?.focus(); }
    };
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [lightbox]);

  return (
    <>
      <section id="explorations" ref={sectionRef} className="relative bg-bg min-h-[300vh] w-full">
        {/* Layer 1: Pinned center content */}
        <div
          ref={contentRef}
          className="h-screen w-full flex items-center justify-center pointer-events-none z-30 relative"
        >
          <div className="max-w-[300px] lg:max-w-[340px] px-6 py-10 rounded-3xl bg-bg/90 text-center pointer-events-auto">
            <div className="inline-flex items-center gap-2 mb-6">
              <span className="w-10 h-px bg-stroke" />
              <span className="text-xs text-muted uppercase tracking-[0.3em]">PERSPECTIVES</span>
              <span className="w-10 h-px bg-stroke" />
            </div>
            <h2 className="font-editorial text-[28px] md:text-[34px] lg:text-[42px] text-text-primary leading-[1.4] tracking-[0.03em] mb-6">
              纷繁之间，<br />见其脉络。
            </h2>
            <p className="text-muted text-sm md:text-base mb-10 max-w-sm mx-auto leading-relaxed">
              区分文件记载、各方主张与分析判断，让每一步推导都有清楚的前提。
            </p>
            <a
              href="#journal"
              className="group relative inline-flex items-center gap-3 px-8 py-3.5 bg-bg border border-transparent rounded-full text-sm text-text-primary transition-colors"
            >
              <span
                className="absolute inset-0 rounded-full p-[2px] bg-gradient-to-r from-[#89AACC] to-[#4E85BF] opacity-0 group-hover:opacity-100 transition-opacity duration-300"
                style={{ margin: "-2px" }}
              >
                <span className="block w-full h-full rounded-full bg-bg" />
              </span>
              <span className="relative z-10">了解分析方式</span>
              <svg
                className="relative z-10 w-4 h-4 transition-transform duration-300 group-hover:translate-x-1 group-hover:-translate-y-1"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path d="M7 17L17 7M17 7H7M17 7V17" />
              </svg>
            </a>
          </div>
        </div>

        {/* Layer 2: Parallax columns */}
        <div className="absolute inset-0 overflow-hidden pointer-events-none z-20">
          <div className="max-w-[1400px] mx-auto h-full px-12 md:px-24 grid grid-cols-2 gap-12 md:gap-40">
            {/* Left column */}
            <div ref={leftColRef} className="flex flex-col gap-40 md:gap-96 pointer-events-auto items-center">
              <div className="h-[20vh]" />
              {leftItems.map((item) => (
                <div key={item.id} className="w-full max-w-[320px]">
                  <ExplorationCard item={item} onClick={() => setLightbox(item)} />
                </div>
              ))}
            </div>

            {/* Right column */}
            <div ref={rightColRef} className="flex flex-col gap-40 md:gap-96 pointer-events-auto items-center">
              <div className="h-[40vh]" />
              {rightItems.map((item) => (
                <div key={item.id} className="w-full max-w-[320px]">
                  <ExplorationCard item={item} onClick={() => setLightbox(item)} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Lightbox */}
      <AnimatePresence>
        {lightbox && (
          <motion.div
            className="fixed inset-0 z-[100] bg-black/95 flex items-center justify-center p-4"
            role="dialog" aria-modal="true" aria-labelledby="exploration-title"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setLightbox(null)}
          >
            <motion.div
              className="relative max-w-6xl w-full"
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="aspect-[16/10] relative rounded-2xl overflow-hidden border border-white/10 shadow-2xl">
                <img
                  src={lightbox.image}
                  alt={lightbox.alt}
                  className="w-full h-full object-cover"
                />
              </div>
              <div className="flex items-center justify-between mt-4 px-2">
                <span id="exploration-title" className="text-lg font-editorial tracking-wide text-white">
                  {lightbox.title} · 视觉配图
                </span>
                <button
                  ref={closeRef}
                  onClick={() => setLightbox(null)}
                  className="text-white/60 hover:text-white text-sm uppercase tracking-widest transition-colors"
                >
                  关闭 [Esc]
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};

export default Explorations;
