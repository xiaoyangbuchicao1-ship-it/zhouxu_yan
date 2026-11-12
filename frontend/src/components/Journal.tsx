import { motion } from "framer-motion";

const entries = [
  { title: "先说清楚，你要解决什么", image: "/explorations/planet.jpeg", readTime: "01", date: "提出问题", description: "说明争议发生的背景，以及你希望得到的判断、核对或草稿。不必先整理成完整报告，也可以从一个具体问题开始。" },
  { title: "让材料，回到事情的脉络里", image: "/explorations/cubes.jpeg", readTime: "02", date: "补充材料", description: "按需要补充合同、提单、邮件和检验报告。保留原始文件与版本，说明文件之间的关系，并区分案件材料与一般参考资料。" },
  { title: "有依据，也看见判断的边界", image: "/explorations/ascii.jpeg", readTime: "03", date: "核对结果", description: "核对引用对应的文件与位置，检查计算起止、扣减和前提。材料没有显示，不等于法律上无效；有分歧的前提需要分别讨论。" },
  { title: "补充新信息，继续向前一步", image: "/explorations/smoke.jpeg", readTime: "04", date: "持续推进", description: "在原对话中继续补充材料或纠正前提，辨明哪些结论改变、哪些仍然成立。涉及重要权利义务和正式提交的内容，交由专业人员复核。" },
];

const ease = [0.25, 0.1, 0.25, 1] as [number, number, number, number];

const ArrowNE = ({ size = 18 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 256 256" fill="currentColor">
    <path d="M200,64V168a8,8,0,0,1-16,0V83.31L69.66,197.66a8,8,0,0,1-11.32-11.32L172.69,72H88a8,8,0,0,1,0-16H192A8,8,0,0,1,200,64Z" />
  </svg>
);

const ViewAllButton = () => (
  <a href="#workflow" className="group relative inline-flex items-center gap-2 px-5 py-2.5 bg-surface border border-stroke rounded-full text-sm text-muted hover:text-text-primary transition-colors duration-300">
    <span className="absolute inset-0 rounded-full p-[2px] bg-gradient-to-r from-[#89AACC] to-[#4E85BF] opacity-0 group-hover:opacity-100 transition-opacity duration-300" style={{ margin: "-1px" }}>
      <span className="block w-full h-full rounded-full bg-surface" />
    </span>
    <span className="relative z-10">查看协作步骤</span>
    <svg className="relative z-10 w-4 h-4 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" viewBox="0 0 256 256" fill="currentColor">
      <path d="M204,64V168a12,12,0,0,1-24,0V93L72.49,200.49a12,12,0,0,1-17-17L163,76H88a12,12,0,0,1,0-24H192A8,8,0,0,1,204,64Z" />
    </svg>
  </a>
);

const Journal = () => {
  return (
    <section id="journal" className="relative bg-bg py-16 md:py-24 overflow-hidden">
      <div className="max-w-[1200px] mx-auto px-6 md:px-10 lg:px-16">
        {/* Header */}
        <motion.div
          className="flex flex-col md:flex-row md:items-end md:justify-between gap-6 mb-12 md:mb-16 px-2"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          transition={{ duration: 1, ease }}
          viewport={{ once: true, margin: "-100px" }}
        >
          <div>
            <div className="inline-flex items-center gap-2 mb-4">
              <span className="w-8 h-px bg-stroke" />
              <span className="text-xs text-muted uppercase tracking-[0.3em]">A CLEARER PROCESS</span>
            </div>
            <h2 className="font-editorial text-3xl md:text-4xl lg:text-5xl text-text-primary leading-[1.45]">
              把问题说清，让分析有据
            </h2>
            <p className="text-muted text-sm md:text-base mt-3 max-w-md">
              从提出问题到核对结果，用清楚的前提，推进每一次协作。点击展开说明。
            </p>
          </div>
          <ViewAllButton />
        </motion.div>

        {/* Entries */}
        <div className="flex flex-col gap-6">
          {entries.map((entry, i) => (
            <motion.details
              key={entry.title}
              className="group p-4 bg-surface/30 hover:bg-surface border border-stroke rounded-[40px] sm:rounded-[56px] transition-all duration-500 ease-out hover:scale-[1.005] hover:shadow-xl hover:shadow-black/5"
              initial={{ opacity: 0, x: i % 2 === 0 ? -20 : 20 }}
              whileInView={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.8, delay: i * 0.1, ease }}
              viewport={{ once: true, margin: "-50px" }}
            >
              <summary className="flex flex-col sm:flex-row items-center gap-6 list-none cursor-pointer rounded-[40px]">
              {/* Image */}
              <div className="relative w-24 h-24 sm:w-[100px] sm:h-[100px] rounded-full overflow-hidden shrink-0 border-2 border-transparent group-hover:border-stroke transition-all duration-500">
                <img
                  src={entry.image}
                  alt=""
                  className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-700 ease-out"
                />
              </div>

              {/* Content */}
              <div className="flex flex-col md:flex-row items-center gap-4 w-full min-w-0 px-4 sm:px-2">
                <h3 className="font-editorial text-lg md:text-2xl text-text-primary font-medium group-hover:translate-x-1 transition-transform duration-300 max-w-[600px] text-center sm:text-left leading-relaxed">
                  {entry.title}
                </h3>
                <div className="hidden lg:block flex-grow h-px bg-stroke/30 group-hover:bg-stroke/60 transition-colors duration-500" />
                <div className="flex items-center gap-3 shrink-0">
                  <span className="text-xs sm:text-sm text-muted font-medium">{entry.readTime}</span>
                  <span className="w-1.5 h-1.5 rounded-full bg-stroke group-hover:bg-[#4E85BF] transition-colors duration-500" />
                  <span className="text-xs sm:text-sm text-muted">{entry.date}</span>
                </div>
              </div>

              {/* Arrow */}
              <div className="w-10 h-10 rounded-full border border-stroke flex items-center justify-center text-text-primary/50 group-hover:bg-text-primary group-hover:text-bg group-hover:border-text-primary transition-all duration-300 shrink-0 ml-4">
                <span className="group-hover:scale-110 transition-transform duration-300 inline-flex">
                  <ArrowNE />
                </span>
              </div>
              </summary>
              <p className="text-sm text-muted leading-[1.95] max-w-3xl px-5 sm:pl-[132px] sm:pr-12 pt-5 pb-4">{entry.description}</p>
            </motion.details>
          ))}
        </div>
      </div>
    </section>
  );
};

export default Journal;
