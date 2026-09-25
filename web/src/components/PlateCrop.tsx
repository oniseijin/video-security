import { useState } from "react"
import { Lightbox } from "./Lightbox"
import type { LightboxFrame } from "./Lightbox"

interface PlateCropProps {
  cropUrl: string | null
  cropSrcUrl: string | null
  cropBox: number[] | null
  alt: string
}

export function PlateCrop({ cropUrl, cropSrcUrl, cropBox, alt }: PlateCropProps) {
  const [open, setOpen] = useState(false)
  if (!cropUrl) {
    return null
  }
  if (!cropSrcUrl || !cropBox || cropBox.length !== 4) {
    return <img alt={alt} src={cropUrl} />
  }
  const frames: LightboxFrame[] = [
    { url: cropSrcUrl, faces: [], caption: alt, rects: [cropBox] },
  ]
  return (
    <>
      <img
        alt={alt}
        className="plate-crop-img"
        onClick={() => setOpen(true)}
        src={cropUrl}
      />
      {open ? (
        <Lightbox
          frames={frames}
          index={0}
          onIndex={() => undefined}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  )
}
