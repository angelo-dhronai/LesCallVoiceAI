import type { Config } from 'tailwindcss'

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter Variable"', 'Inter', 'system-ui', 'sans-serif'],
        display: ['"Plus Jakarta Sans Variable"', '"Plus Jakarta Sans"', 'Inter', 'sans-serif'],
      },
      colors: {
        mint: {
          DEFAULT: '#00F5A0',
          50: '#e9fff7', 100: '#c9ffec', 200: '#8fffd6', 300: '#4dffbe',
          400: '#13f4a3', 500: '#00d98c', 600: '#00b073', 700: '#00895b', 800: '#066b49', 900: '#08573d',
        },
        indigo: {
          DEFAULT: '#4F46E5',
          50: '#eef0ff', 100: '#e0e3ff', 200: '#c6cbff', 300: '#a3a8ff',
          400: '#817fff', 500: '#4F46E5', 600: '#4a3fd4', 700: '#3f33b3', 800: '#342c90', 900: '#2d2873',
        },
        ink: {
          DEFAULT: '#0b1220',
          soft: '#3a4256',
          dim: '#6b7488',
        },
      },
      borderRadius: {
        xl: '12px', '2xl': '16px', '3xl': '22px',
      },
      boxShadow: {
        glass: '0 4px 30px rgba(15,23,42,0.05), 0 1px 2px rgba(15,23,42,0.04)',
        'glass-lg': '0 18px 50px -12px rgba(15,23,42,0.16), 0 2px 8px rgba(15,23,42,0.05)',
        'glow-mint': '0 0 0 1px rgba(0,245,160,0.25), 0 8px 30px -8px rgba(0,217,140,0.35)',
        'glow-indigo': '0 0 0 1px rgba(79,70,229,0.25), 0 8px 30px -8px rgba(79,70,229,0.4)',
      },
      backgroundImage: {
        'mesh-light': 'radial-gradient(1200px 600px at 10% -10%, rgba(0,245,160,0.10), transparent 55%), radial-gradient(1000px 500px at 100% 0%, rgba(79,70,229,0.10), transparent 50%)',
        'mesh-dark': 'radial-gradient(1200px 600px at 10% -10%, rgba(0,245,160,0.12), transparent 55%), radial-gradient(1000px 500px at 100% 0%, rgba(79,70,229,0.18), transparent 50%)',
      },
      keyframes: {
        'fade-up': { '0%': { opacity: '0', transform: 'translateY(8px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        'pulse-soft': { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.45' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: {
        'fade-up': 'fade-up .5s cubic-bezier(.22,1,.36,1) both',
        'pulse-soft': 'pulse-soft 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
