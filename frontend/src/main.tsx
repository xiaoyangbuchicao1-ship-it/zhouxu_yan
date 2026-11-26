import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";
import "./styles/tokens.css";
import "./styles/kit.css";

createRoot(document.getElementById("root")!).render(<App />);
