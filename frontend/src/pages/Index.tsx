import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import LoadingScreen from "@/components/LoadingScreen";
import Hero from "@/components/Hero";
import SelectedWorks from "@/components/SelectedWorks";
import Journal from "@/components/Journal";
import Explorations from "@/components/Explorations";
import Contact from "@/components/Contact";

const Index = () => {
  const [isLoading, setIsLoading] = useState(true);

  return (
    <>
      <AnimatePresence mode="wait">
        {isLoading && (
          <LoadingScreen onComplete={() => setIsLoading(false)} />
        )}
      </AnimatePresence>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: isLoading ? 0 : 1 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
      >
        <Hero />
        <SelectedWorks />
        <Journal />
        <Explorations />
        <Contact />
      </motion.div>
    </>
  );
};

export default Index;
