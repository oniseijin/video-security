import { useState } from "react"
import { useNavigate } from "react-router-dom"

export function SearchBox() {
  const [q, setQ] = useState("")
  const navigate = useNavigate()
  return (
    <form
      className="search-box"
      onSubmit={(event) => {
        event.preventDefault()
        const query = q.trim()
        navigate(query ? `/search?q=${encodeURIComponent(query)}` : "/search")
      }}
    >
      <input
        type="search"
        value={q}
        placeholder="SEARCH"
        aria-label="Search"
        onChange={(event) => setQ(event.target.value)}
      />
      <button type="submit">GO</button>
    </form>
  )
}
