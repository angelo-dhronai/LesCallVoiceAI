/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // LesCall brand: navy wordmark + green accent.
        brand: { DEFAULT: '#134e68', dark: '#0d3a4f', light: '#1c6688' },
        accent: { DEFAULT: '#28b16a', dark: '#1f9457' },
      },
    },
  },
  plugins: [],
}
