/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./templates/**/*.html",
    "./*/templates/**/*.html",
    "./*/forms.py",
    "./core/forms.py",
  ],
  // Category colours come from the database and the live stream builds class
  // names in JavaScript, so the scanner can't see either. Keep both alive.
  safelist: [
    // Only the shades the dynamic code paths actually emit — a wider net doubles
    // the stylesheet for classes nothing ever uses.
    { pattern: /bg-(brand|azure|flame|rose|amber|emerald|sky|violet|orange|teal|slate)-500\/(5|10)/ },
    { pattern: /text-(brand|azure|flame|rose|amber|emerald|sky|violet|orange|teal|slate)-(400|600|700)/ },
    { pattern: /text-(brand|azure|flame|rose|amber|emerald|sky|violet|orange|teal|slate)-400/, variants: ["dark"] },
    { pattern: /bg-(brand|azure|flame|rose|amber|emerald|sky|violet|orange|teal)-(50|100|500|600)/ },
    { pattern: /border-(brand|azure|flame|rose|amber|emerald|sky|violet|orange|teal)-500\/30/ },
  ],
  theme: {
    extend: {
      colors: {
        // Deep indigo navy — the header colour from the design. The primary.
        brand: {
          50: "#eef0fa",
          100: "#dcdff4",
          200: "#bcc2e9",
          300: "#939cd8",
          400: "#6b74c2",
          500: "#4f56a8",
          600: "#3d4189",
          700: "#33366e",
          800: "#2b2d58",
          900: "#232448",
          950: "#14152b",
        },
        // The orange of the logo mark, used for highlights and the badge dots.
        flame: {
          50: "#fff6ed",
          100: "#ffead5",
          200: "#fed1aa",
          300: "#fdb174",
          400: "#fb873c",
          500: "#f97316",
          600: "#ea580c",
          700: "#c2410c",
          800: "#9a3412",
          900: "#7c2d12",
          950: "#431407",
        },
        // Ceramic blue — porcelain through to delft. The secondary.
        azure: {
          50: "#f0f6fa",
          100: "#dcebf3",
          200: "#bad8e8",
          300: "#8fbdd6",
          400: "#5f9dc0",
          500: "#4180a6",
          600: "#33668a",
          700: "#2c5470",
          800: "#29465d",
          900: "#263c4f",
          950: "#152735",
        },
        // Semantic surfaces, driven by CSS variables so one token flips the theme.
        surface: "rgb(var(--surface) / <alpha-value>)",
        "surface-2": "rgb(var(--surface-2) / <alpha-value>)",
        "surface-3": "rgb(var(--surface-3) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        "ink-muted": "rgb(var(--ink-muted) / <alpha-value>)",
        "ink-subtle": "rgb(var(--ink-subtle) / <alpha-value>)",
        hairline: "rgb(var(--hairline) / <alpha-value>)",
      },
      fontFamily: {
        sans: [
          "Inter var",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        display: ["Outfit", "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      letterSpacing: {
        tightest: "-0.045em",
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(35 36 72 / 0.06), 0 8px 24px -12px rgb(35 36 72 / 0.20)",
        lift: "0 18px 45px -18px rgb(61 65 137 / 0.45)",
        glow: "0 0 0 1px rgb(79 86 168 / 0.20), 0 12px 40px -12px rgb(79 86 168 / 0.45)",
        inset: "inset 0 1px 0 0 rgb(255 255 255 / 0.06)",
      },
      backgroundImage: {
        // Indigo night with the logo's orange bleeding through, as in the design.
        "mesh-brand":
          "radial-gradient(at 12% 18%, rgb(79 86 168 / 0.70) 0px, transparent 55%), radial-gradient(at 85% 8%, rgb(65 128 166 / 0.50) 0px, transparent 52%), radial-gradient(at 72% 90%, rgb(249 115 22 / 0.30) 0px, transparent 50%), radial-gradient(at 22% 88%, rgb(43 45 88 / 0.60) 0px, transparent 55%)",
        "grid-faint":
          "linear-gradient(rgb(148 163 184 / 0.10) 1px, transparent 1px), linear-gradient(90deg, rgb(148 163 184 / 0.10) 1px, transparent 1px)",
        shimmer:
          "linear-gradient(90deg, transparent, rgb(148 163 184 / 0.18), transparent)",
      },
      backgroundSize: {
        grid: "44px 44px",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          "0%": { opacity: "0", transform: "scale(0.96)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        "float-slow": {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-14px)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.5s cubic-bezier(0.16, 1, 0.3, 1) both",
        "scale-in": "scale-in 0.2s cubic-bezier(0.16, 1, 0.3, 1) both",
        shimmer: "shimmer 1.6s infinite",
        "float-slow": "float-slow 9s ease-in-out infinite",
      },
      transitionTimingFunction: {
        spring: "cubic-bezier(0.16, 1, 0.3, 1)",
      },
    },
  },
  plugins: [],
};
