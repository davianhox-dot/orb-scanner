import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0B0E11",
        panel: "#12151B",
        "panel-raised": "#171B22",
        border: "#1E232B",
        "text-primary": "#E6E9EF",
        "text-muted": "#8B93A1",
        "text-dim": "#5A6272",
        gain: "#00D68F",
        loss: "#FF4D4F",
        catalyst: "#F5A623",
        info: "#4C9AFF",
      },
      fontFamily: {
        display: ["var(--font-display)", "sans-serif"],
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      boxShadow: {
        row: "inset 0 -1px 0 0 #1E232B",
      },
    },
  },
  plugins: [],
};

export default config;
