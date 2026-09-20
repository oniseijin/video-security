import { useState } from "react"
import { setThemeAttr, themeAttr } from "../theme"
import type { Theme } from "../theme"

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(themeAttr)
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={() => {
        const next: Theme = theme === "samaritan" ? "machine" : "samaritan"
        setThemeAttr(next)
        try {
          localStorage.setItem("vs-theme", next)
        } catch {}
        setTheme(next)
      }}
    >
      {theme === "samaritan" ? "Samaritan" : "Machine"}
    </button>
  )
}
