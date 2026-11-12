import { motion } from "framer-motion";
import { plannedModules } from "@/lib/landing-content";

const projects = [
  { slug: "legal-research", title: "查明依据", subtitle: "法律检索 · 案例分析", image: "/projects/wireframe.png", gradient: "from-violet-500 via-fuchsia-400/60 via-indigo-500/60 to-transparent" },
  { slug: "contract-review", title: "审视约定", subtitle: "合同审查 · 案件研判", image: "/projects/building.png", gradient: "from-sky-500 via-blue-400/60 to-transparent" },
  { slug: "evidence", title: "理清事实", subtitle: "证据整理 · 索赔辅助", image: "/projects/person.png", gradient: "from-emerald-500 via-emerald-300/60 via-teal-500/60 to-transparent" },
  { slug: "legal-drafting", title: "推进工作", subtitle: "文书起草 · 案件协同", image: "/projects/branding.png", gradient: "from-amber-500 via-amber-300/60 via-orange-500/60 to-transparent" },
];

const colSpans = [
  "md:col-span-7",
  "md:col-span-5",
  "md:col-span-5",
  "md:col-span-7",
];

const aspectClasses = [
  "aspect-[4/3]",
  "aspect-[4/3] md:aspect-auto md:h-full",
  "aspect-[4/3] md:aspect-auto md:h-full",
  "aspect-[4/3]",
];

const ease = [0.25, 0.1, 0.25, 1] as [number, number, number, number];

const ViewAllButton = ({ className = "" }: { className?: string }) => (
  <a href="#roadmap" className={`group relative inline-flex items-center gap-3 px-5 py-3 bg-bg border-2 border-stroke rounded-full text-sm text-muted transition-colors ${className}`}>
    <span className="absolute inset-0 rounded-full p-[2px] bg-gradient-to-r from-[#89AACC] to-[#4E85BF] opacity-0 group-hover:opacity-100 transition-opacity duration-300" style={{ margin: "-2px" }}>
      <span className="block w-full h-full rounded-full bg-bg" />
    </span>
    <span className="relative z-10">查看功能规划</span>
    <svg className="relative z-10 w-4 h-4 transition-transform duration-300 group-hover:translate-x-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path d="M5 12h14M12 5l7 7-7 7" />
    </svg>
  </a>
);

const SelectedWorks = () => {
  return (
    <section id="work" className="bg-bg py-12 md:py-16 overflow-hidden">
      <div className="max-w-[1200px] mx-auto px-6 md:px-10 lg:px-16">
        {/* Header */}
        <motion.div
          className="flex flex-col md:flex-row md:items-end md:justify-between mb-12 md:mb-16 px-2"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          transition={{ duration: 1, ease }}
          viewport={{ once: true, margin: "-100px" }}
        >
          <div>
            <div className="inline-flex items-center gap-2 mb-4">
              <span className="w-8 h-px bg-stroke" />
              <span className="text-xs text-muted uppercase tracking-[0.3em]">WORK &amp; RESEARCH</span>
            </div>
            <h2 className="font-editorial text-3xl md:text-4xl lg:text-5xl text-text-primary leading-[1.45]">
              从查明依据，到形成判断
            </h2>
            <p className="text-muted text-sm md:text-base mt-3 max-w-md">
              围绕海事争议的工作过程，逐步连接研究、审查、证据、索赔与案件协作。
            </p>
          </div>
          <ViewAllButton className="hidden md:inline-flex" />
        </motion.div>

        {/* Bento Grid */}
        <div className="grid grid-cols-1 md:grid-cols-12 gap-5 md:gap-6 px-2">
          {projects.map((project, i) => (
            <motion.div
              key={project.slug}
              className={`${colSpans[i]}`}
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 1, delay: i * 0.1, ease }}
              viewport={{ once: true, margin: "-100px" }}
            >
              <a href={`#${project.slug}`} aria-label={`了解${project.title}的功能规划`} className={`group block bg-surface border border-stroke rounded-3xl ${aspectClasses[i]} relative overflow-hidden transition-colors duration-300 hover:border-transparent`}>
                {/* Background image */}
                <div className="absolute inset-0">
                  {project.image ? (
                    <img
                      src={project.image}
                      alt=""
                      className="w-full h-full object-cover transition-transform duration-500 ease-out group-hover:scale-105"
                    />
                  ) : (
                    <div className={`w-full h-full bg-gradient-to-br ${project.gradient}`} />
                  )}
                  {/* Halftone overlay */}
                  <div
                    className="absolute inset-0 opacity-20 mix-blend-multiply"
                    style={{
                      backgroundImage: "radial-gradient(circle, #000 1px, transparent 1px)",
                      backgroundSize: "4px 4px",
                    }}
                  />
                </div>

                {/* Hover overlay */}
                <div className="absolute inset-0 bg-bg/70 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                <div className="absolute inset-0 backdrop-blur-[0px] group-hover:backdrop-blur-lg opacity-0 group-hover:opacity-100 transition-all duration-500" />

                {/* Hover label */}
                <div className="absolute inset-0 flex items-center justify-center z-10 opacity-0 translate-y-2 group-hover:opacity-100 group-hover:translate-y-0 transition-all duration-300">
                  <div className={`rounded-full p-[1px] animated-gradient-border bg-gradient-to-r ${project.gradient}`}>
                    <div className="px-4 py-2 md:px-5 rounded-full bg-white text-black text-xs md:text-base font-medium">
                      <span className="font-body">了解 — </span>
                      <span className="font-editorial">{project.title}</span>
                    </div>
                  </div>
                </div>
                <div className="absolute inset-x-0 bottom-0 z-[5] px-6 pt-14 pb-5 bg-gradient-to-t from-black/85 to-transparent pointer-events-none">
                  <p className="font-editorial text-xl text-white">{project.title}</p>
                  <p className="text-xs text-white/75 mt-2">{project.subtitle}</p>
                </div>
              </a>
            </motion.div>
          ))}
        </div>

        {/* Mobile button */}
        <div className="flex justify-center md:hidden mt-12">
          <ViewAllButton />
        </div>
        <div id="roadmap" className="mt-16 md:mt-24 px-2">
          <span className="text-xs text-muted tracking-[0.25em]">PRODUCT ROADMAP</span>
          <h3 className="font-editorial text-2xl md:text-3xl mt-4">八个方向，一条工作脉络。</h3>
          <p className="text-sm text-muted leading-[1.9] mt-4 max-w-2xl">以下为后续建设规划，不代表各模块已经开放使用。配图沿用原模板，仅作视觉表达，不是案件实图或产品输出。</p>
          <div className="grid md:grid-cols-2 gap-x-12 mt-8">
            {plannedModules.map((module, i) => (
              <article key={module.id} id={module.id} className="border-t border-stroke py-6">
                <p className="text-[11px] text-muted tracking-[0.14em] mb-3">{String(i + 1).padStart(2, "0")} / {module.english}</p>
                <h4 className="font-editorial text-xl mb-3">{module.title}</h4>
                <p className="text-sm leading-[1.9] text-muted">{module.description}</p>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

export default SelectedWorks;
