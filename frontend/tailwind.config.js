/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#f6f5f0",
        panel: "#fdfcf8",
        panel2: "#edf0e5",
        border: "#dddfd5",
        fg: "#24372f",
        muted: "#5d6957",
        accent: "#b94327",
      },
    },
  },
  plugins: [],
};
