import { motion } from "motion/react";
import reactLogo from "./assets/react.svg";

function App() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 p-8 text-center">
      <motion.header
        className="flex flex-col items-center gap-2"
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
      >
        <img
          src={reactLogo}
          className="h-24 w-24 transition-[filter] duration-300 hover:drop-shadow-[0_0_2em_#6366f1]"
          alt="React logo"
        />
        <h1 className="m-0 text-5xl font-bold">File Converter</h1>
        <p className="m-0 text-white/60">
          React + Vite + TypeScript +{" "}
          <a
            href="https://motion.dev"
            target="_blank"
            rel="noopener noreferrer"
            className="text-indigo-400 hover:text-indigo-300"
          >
            Motion
          </a>{" "}
          +{" "}
          <a
            href="https://tailwindcss.com"
            target="_blank"
            rel="noopener noreferrer"
            className="text-indigo-400 hover:text-indigo-300"
          >
            Tailwind
          </a>
        </p>
      </motion.header>

      <motion.button
        className="cursor-pointer rounded-full border-none bg-indigo-500 px-7 py-3 text-base font-semibold text-white hover:bg-indigo-600"
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        onClick={() => window.alert("File conversion coming soon!")}
      >
        Convert a file
      </motion.button>

      <motion.div
        className="rounded-full border border-slate-700 bg-slate-800 px-4 py-1.5 text-sm text-white/80"
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ delay: 0.4, type: "spring", stiffness: 260, damping: 20 }}
      >
        Powered by motion
      </motion.div>
    </div>
  );
}

export default App;
