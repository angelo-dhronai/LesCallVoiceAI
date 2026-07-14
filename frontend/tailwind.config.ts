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
        // "mint" kept as the legacy name — values are now a refined emerald
        // so every existing mint-* usage inherits the calmer palette.
        mint: {
          DEFAULT: '#10b981',
          50: '#ecfdf5', 100: '#d1fae5', 200: '#a7f3d0', 300: '#6ee7b7',
          400: '#34d399', 500: '#10b981', 600: '#059669', 700: '#047857', 800: '#065f46', 900: '#064e3b',
        },
        indigo: {
          DEFAULT: '#4F46E5',
          50: '#eef2ff', 100: '#e0e7ff', 200: '#c7d2fe', 300: '#a5b4fc',
          400: '#818cf8', 500: '#6366f1', 600: '#4f46e5', 700: '#4338ca', 800: '#3730a3', 900: '#312e81',
        },
        ink: {
          DEFAULT: '#101014',
          soft: '#3f3f46',
          dim: '#6f7180',
        },
      },
      borderRadius: {
        xl: '12px', '2xl': '14px', '3xl': '18px',
      },
      boxShadow: {
        glass: '0 1px 2px rgba(16,16,20,0.04)',
        'glass-lg': '0 1px 2px rgba(16,16,20,0.04), 0 12px 32px -12px rgba(16,16,20,0.18)',
        // Legacy names, subdued: a hairline tinted ring + soft ambient.
        'glow-mint': '0 0 0 1px rgba(16,185,129,0.18), 0 1px 2px rgba(16,16,20,0.05)',
        'glow-indigo': '0 0 0 1px rgba(79,70,229,0.18), 0 1px 2px rgba(16,16,20,0.05)',
      },
      keyframes: {
        'fade-up': { '0%': { opacity: '0', transform: 'translateY(6px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        'slide-in-right': { '0%': { opacity: '0', transform: 'translateX(24px)' }, '100%': { opacity: '1', transform: 'translateX(0)' } },
        'pulse-soft': { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.45' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: {
        'fade-up': 'fade-up .35s cubic-bezier(.22,1,.36,1) both',
        'slide-in-right': 'slide-in-right .3s cubic-bezier(.22,1,.36,1) both',
        'pulse-soft': 'pulse-soft 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
