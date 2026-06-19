/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        // Single primary accent used for emphasis across the whole app.
        accent: {
          DEFAULT: '#3B5BFF',
          dark: '#2c46d6',
          light: '#6f86ff',
          soft: '#eef1ff',
        },
        // LesCall brand marks (wordmark / live dot) kept available.
        brand: { DEFAULT: '#134e68', dark: '#0d3a4f', light: '#1c6688' },
      },
      boxShadow: {
        flyout: '0 10px 40px -8px rgba(15, 23, 42, 0.25)',
        pop: '0 8px 24px -6px rgba(15, 23, 42, 0.18)',
      },
    },
  },
  plugins: [],
}
