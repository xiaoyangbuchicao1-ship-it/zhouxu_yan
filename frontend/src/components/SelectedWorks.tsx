import { motion } from "framer-motion";
import { Link } from "react-router-dom";

const projects = [
  { slug: "legal-research", title: "查明依据", subtitle: "海事条文检索 · 现行与历史版本", image: "/projects/wireframe.png", gradient: "from-violet-500 via-fuchsia-400/60 via-indigo-500/60 to-transparent" },
  { slug: "contract-review", title: "对照案例", subtitle: "海事案例检索 · 判决与法院认定", image: "/projects/building.png", gradient: "from-sky-500 via-blue-400/60 to-transparent" },
  { slug: "evidence", title: "阅读文献", subtitle: "海事论文检索 · 观点与期刊页码", image: "/projects/person.png", gradient: "from-emerald-500 via-emerald-300/60 via-teal-500/60 to-transparent" },
  { slug: "legal-drafting", title: "形成稿件", subtitle: "论文写作 · 导出 PDF 与 Word", image: "/projects/branding.png", gradient: "from-amber-500 via-amber-300/60 via-orange-500/60 to-transparent" },
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
            <h2 className="font-editorial text-3xl md:text-4xl lg:text-5xl text-text-primary leading-[1.45] text-balance">
              从查明依据，到形成判断
            </h2>
            <p className="text-muted text-sm md:text-base mt-3 max-w-md">
              检索法规、案例与论文，核对依据，再写成可交付的稿件。
            </p>
          </div>
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
              <Link to="/workspace" aria-label={`试用${project.title}`} className={`group block bg-surface border border-stroke rounded-3xl ${aspectClasses[i]} relative overflow-hidden transition-colors duration-300 hover:border-transparent`}>
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
                      <span className="font-body">试用 — </span>
                      <span className="font-editorial">{project.title}</span>
                    </div>
                  </div>
                </div>
                <div className="absolute inset-x-0 bottom-0 z-[5] px-6 pt-14 pb-5 bg-gradient-to-t from-black/85 to-transparent pointer-events-none">
                  <p className="font-editorial text-xl text-white">{project.title}</p>
                  <p className="text-xs text-white/75 mt-2">{project.subtitle}</p>
                </div>
              </Link>
            </motion.div>
          ))}
        </div>

        <p className="mt-12 md:mt-16 px-2 text-sm text-muted">更多能力正在持续扩展中。</p>
      </div>
    </section>
  );
};

export default SelectedWorks;
