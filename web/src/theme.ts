import tokens from "./theme/tokens.json"

export type Theme = "machine" | "samaritan"

export type Tone = "threat" | "warning" | "info" | "asset"

export interface ThemeTokens {
  bg: string
  surface_1: string
  surface_2: string
  line: string
  line_faint: string
  ink: string
  ink_dim: string
  ink_faint: string
  accent: string
  threat: string
  asset: string
  info: string
  success: string
  warning: string
  selection_bg: string
  selection_ink: string
}

export interface TokenSet {
  machine: ThemeTokens
  samaritan: ThemeTokens
  tones: Record<Tone, string>
}

export const TOKENS: TokenSet = tokens

export function toneColor(tone: Tone): string {
  return TOKENS.tones[tone]
}

export function themeAttr(): Theme {
  return document.documentElement.getAttribute("data-theme") === "samaritan"
    ? "samaritan"
    : "machine"
}

export function setThemeAttr(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme)
}
