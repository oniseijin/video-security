import { useCallback, useEffect, useRef, useState } from "react"
import type { EventKeyframeBox } from "../api"
import { BoxOverlay } from "./BoxOverlay"
import { FaceBoxes, toFaceBoxes } from "./FaceBoxes"

export interface LightboxFrame {
  url: string
  faces: number[][]
  boxes?: EventKeyframeBox[]
  caption: string
  rects?: number[][]
}

interface LightboxProps {
  frames: LightboxFrame[]
  index: number
  onIndex: (index: number) => void
  onClose: () => void
}

export function Lightbox({ frames, index, onIndex, onClose }: LightboxProps) {
  const zoomRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  const [tx, setTx] = useState(0)
  const [ty, setTy] = useState(0)
  const [dragging, setDragging] = useState(false)
  const drag = useRef({ x: 0, y: 0, tx: 0, ty: 0 })
  const frame = frames[index]
  const open = index >= 0 && frame !== undefined
  const effTx = scale <= 1 ? 0 : tx
  const effTy = scale <= 1 ? 0 : ty

  const reset = useCallback(() => {
    setScale(1)
    setTx(0)
    setTy(0)
  }, [])

  const zoomBy = useCallback(
    (factor: number, clientX?: number, clientY?: number) => {
      setScale((s0) => {
        const s1 = Math.min(8, Math.max(1, s0 * factor))
        if (s1 === s0) {
          return s0
        }
        if (clientX !== undefined && clientY !== undefined && zoomRef.current) {
          const rect = zoomRef.current.getBoundingClientRect()
          const cx = clientX - (rect.left + rect.width / 2)
          const cy = clientY - (rect.top + rect.height / 2)
          setTx((t) => t - (cx / s0) * (s1 - s0))
          setTy((t) => t - (cy / s0) * (s1 - s0))
        }
        return s1
      })
    },
    []
  )

  useEffect(() => {
    reset()
  }, [index, reset])

  useEffect(() => {
    if (!open) {
      return
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose()
      } else if (e.key === "ArrowRight") {
        onIndex(Math.min(frames.length - 1, index + 1))
      } else if (e.key === "ArrowLeft") {
        onIndex(Math.max(0, index - 1))
      }
    }
    window.addEventListener("keydown", onKey)
    document.body.style.overflow = "hidden"
    return () => {
      window.removeEventListener("keydown", onKey)
      document.body.style.overflow = ""
    }
  }, [open, index, frames.length, onClose, onIndex])

  useEffect(() => {
    if (!open) {
      return
    }
    const el = zoomRef.current
    if (!el) {
      return
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      zoomBy(e.deltaY < 0 ? 1.25 : 0.8, e.clientX, e.clientY)
    }
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
  }, [open, zoomBy])

  if (!open || !frame) {
    return null
  }

  const startDrag = (e: React.MouseEvent) => {
    if (scale <= 1) {
      return
    }
    e.preventDefault()
    setDragging(true)
    drag.current = { x: e.clientX, y: e.clientY, tx: effTx, ty: effTy }
  }

  const moveDrag = (e: React.MouseEvent) => {
    if (!dragging) {
      return
    }
    setTx(drag.current.tx + (e.clientX - drag.current.x))
    setTy(drag.current.ty + (e.clientY - drag.current.y))
  }

  return (
    <div
      aria-hidden={false}
      className="lightbox open"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose()
        }
      }}
    >
      <div className="lightbox-frame">
        <div
          className="lightbox-zoom"
          ref={zoomRef}
          style={{ transform: `translate(${effTx}px, ${effTy}px) scale(${scale})` }}
        >
          <img
            alt={frame.caption}
            className={dragging ? "dragging" : scale > 1 ? "zoomed" : ""}
            draggable={false}
            onDoubleClick={() => {
              if (scale > 1) {
                reset()
              } else {
                setScale(2.5)
              }
            }}
            onMouseDown={startDrag}
            onMouseLeave={() => setDragging(false)}
            onMouseMove={moveDrag}
            onMouseUp={() => setDragging(false)}
            src={frame.url}
          />
          <FaceBoxes boxes={toFaceBoxes(frame.faces)} />
          <BoxOverlay boxes={frame.boxes} />
          {frame.rects ? (
            <FaceBoxes className="plate-box" boxes={toFaceBoxes(frame.rects)} />
          ) : null}
        </div>
        <div className="lightbox-controls" onClick={(e) => e.stopPropagation()}>
          <button onClick={() => zoomBy(1.25)} type="button">+</button>
          <span className="lb-level">{Math.round(scale * 100)}%</span>
          <button onClick={() => zoomBy(0.8)} type="button">−</button>
          <button onClick={reset} type="button">RESET</button>
          <button onClick={onClose} type="button">CLOSE</button>
        </div>
        <p className="lightbox-caption">{frame.caption}</p>
      </div>
    </div>
  )
}
