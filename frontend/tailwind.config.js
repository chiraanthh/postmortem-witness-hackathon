/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Warm near-black grounds, lifted from the reference screenshot.
        base: "#0a0806",
        base2: "#0d0a08",
        panel: "#151009",
        panel2: "#1c150d",
        raised: "#241a10",
        line: "#2a2016",
        line2: "#3a2c1c",
        // Brand accent — burnt orange.
        accent: "#ec6a2c",
        accentSoft: "#f0864f",
        accentDim: "#8a4321",
        // Text.
        ink: "#f6ede1",
        inkMute: "#b7a891",
        inkFaint: "#7c6e5c",
        // Hypothesis states.
        open: "#e6b24d",
        ruled: "#7d7266",
        confirm: "#ec6a2c",
        danger: "#ff5a4d",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Inter",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        display: [
          "Space Grotesk",
          "-apple-system",
          "Segoe UI",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        led: [
          "Silkscreen",
          "Share Tech Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 18px 40px -24px rgba(0,0,0,0.9)",
        glow: "0 0 0 1px rgba(236,106,44,0.35), 0 0 28px -6px rgba(236,106,44,0.5)",
      },
      keyframes: {
        rowIn: {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        correctPulse: {
          "0%": { boxShadow: "0 0 0 0 rgba(236,106,44,0.0)", backgroundColor: "rgba(236,106,44,0.22)" },
          "35%": { boxShadow: "0 0 0 2px rgba(236,106,44,0.55)", backgroundColor: "rgba(236,106,44,0.16)" },
          "100%": { boxShadow: "0 0 0 0 rgba(236,106,44,0.0)", backgroundColor: "rgba(236,106,44,0.0)" },
        },
        moveGlow: {
          "0%": { boxShadow: "0 0 0 0 rgba(236,106,44,0.0)" },
          "30%": { boxShadow: "0 0 0 2px rgba(236,106,44,0.6), 0 0 26px -4px rgba(236,106,44,0.7)" },
          "100%": { boxShadow: "0 0 0 0 rgba(236,106,44,0.0)" },
        },
        spinSlow: {
          to: { transform: "rotate(360deg)" },
        },
        blink: {
          "0%, 60%": { opacity: "1" },
          "80%, 100%": { opacity: "0.25" },
        },
      },
      animation: {
        rowIn: "rowIn 0.35s ease-out both",
        correctPulse: "correctPulse 2.8s ease-out",
        moveGlow: "moveGlow 1.6s ease-out",
        spinSlow: "spinSlow 3.5s linear infinite",
        blink: "blink 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
