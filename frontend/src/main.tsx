import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "motion/react";
import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/instrument-sans";
import "@fontsource-variable/jetbrains-mono";
import App from "./App";
import { TooltipProvider } from "./components/ui/Tooltip";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <TooltipProvider>
        <App />
      </TooltipProvider>
    </MotionConfig>
  </StrictMode>,
);
