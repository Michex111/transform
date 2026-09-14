---
name: Transform — Format Morph
colors:
  background: '#121417'
  on-background: '#ECEEF1'
  surface: '#1B1E23'
  surface-variant: '#202429'
  on-surface: '#ECEEF1'
  on-surface-variant: '#8A919B'
  outline: '#2A2E34'
  outline-variant: '#343940'
  primary: '#5A6BFF'
  on-primary: '#0B0D12'
  primary-container: '#2A2F55'
  on-primary-container: '#D6DCFF'
  secondary: '#8A919B'
  on-secondary: '#121417'
  error: '#F43F5E'
  on-error: '#121417'
  warning: '#F5A623'
  on-warning: '#121417'
  success: '#22C55E'
  on-success: '#121417'
typography:
  display-lg:
    fontFamily: 'Space Grotesk'
    fontSize: 48px
    fontWeight: '600'
    lineHeight: 52px
    letterSpacing: '-1px'
  headline-lg:
    fontFamily: 'Space Grotesk'
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 34px
  title-lg:
    fontFamily: 'Space Grotesk'
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 26px
  body-lg:
    fontFamily: 'Inter'
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: 'Inter'
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: 'Inter'
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: '0.5px'
  code-sm:
    fontFamily: 'JetBrains Mono'
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
---

# Transform — Format Morph Design System

## Personality
Dependable, engineered, precise. A conversion instrument, not a toy. Dark, high-contrast "precision graphite" theme.

## Signature — the Format Morph
The atomic unit of the brand: two format chips (source → target) joined by an animated gradient stream that flows from the source format's color to the target's. Use it in the logo, the Convert hero, every Queue/History row, and empty states.

Format colors (canonical, on chips + morph streams only):
- PDF #FF5A5F
- Word #2B7CD3
- Excel #1FA463
- Image #E8549C
- Audio #9B59D0
- Video #F97316
- Text #B8C1C9

## Background animation
Landing and Convert use a slow ambient field of faint format glyphs (PDF, DOCX, XLSX, MP3, PNG) drifting and morphing at ~5% opacity, respecting prefers-reduced-motion. Never a generic radial mesh gradient.

## Motion
Ornamental motion belongs to the morph stream only. Status: PENDING/PROCESSING pulse subtly; COMPLETED static emerald; FAILED static rose. Progress bars draw source-color → target-color gradient.

## Layout
12-column grid, 24px gutters, 8px baseline rhythm. App shell: 256px sidebar (collapsible to 72px icon rail) + fluid content. Dense tables 8px row spacing; forms 24-32px. Mobile: single column, 16px margins, bottom nav. Border radius 8px.

## Buttons
Primary: filled brand #5A6BFF, 8px radius, sentence case. Secondary: 1px outline. Destructive: text rose. A control says exactly what it does.

## Copy voice
Active voice, sentence case, plain verbs. Name things by what people control. Errors explain what happened and how to fix it. Empty states are an invitation to act.
