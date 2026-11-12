import { motion } from "framer-motion";

const stats = [
  { number: "01", label: "提出问题", sublabel: "说明你想判断、核对或完成什么。可以先提问，再按需要补充材料。" },
  { number: "02", label: "补充材料", sublabel: "加入合同、提单、往来邮件或扫描件，保留材料的来源与上下文。" },
  { number: "03", label: "核对与继续", sublabel: "查看依据、计算前提与不确定项，补充信息后继续分析。" },
];

const ease = [0.25, 0.1, 0.25, 1] as [number, number, number, number];

const Stats = () => {
  return (
    <section id="workflow" className="relative bg-bg py-16 md:py-24">
      <div className="max-w-[1200px] mx-auto px-6 md:px-10 lg:px-16">
        {/* Header */}
        <motion.div
          className="mb-12 md:mb-16 px-2"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          transition={{ duration: 1, ease }}
          viewport={{ once: true, margin: "-100px" }}
        >
          <div className="inline-flex items-center gap-2 mb-4">
            <span className="w-8 h-px bg-stroke" />
            <span className="text-xs text-muted uppercase tracking-[0.3em]">HOW IT WORKS</span>
          </div>
          <h2 className="font-editorial text-3xl md:text-4xl lg:text-5xl text-text-primary leading-[1.45]">
            从一个具体问题开始
          </h2>
          <p className="text-muted text-sm md:text-base mt-3 max-w-2xl">
            不必一次备齐所有材料。先说清眼前的问题，在同一对话中逐步补充、核对，让分析随着信息一起推进。
          </p>
        </motion.div>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-12 gap-y-16 lg:gap-16">
          {stats.map((stat, i) => (
            <motion.div
              key={stat.label}
              className={`flex flex-col gap-6 ${i === 2 ? "sm:col-span-2 lg:col-span-1" : ""}`}
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: i * 0.1 }}
              viewport={{ once: true }}
            >
              <span className="text-6xl sm:text-7xl md:text-8xl lg:text-9xl font-display text-text-primary tracking-tighter">
                {stat.number}
              </span>
              <div className="w-full h-px bg-stroke" />
              <div>
                <p className="font-editorial text-xl md:text-2xl font-medium text-text-primary mb-2">{stat.label}</p>
                <p className="text-sm text-muted leading-[1.9]">{stat.sublabel}</p>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default Stats;
