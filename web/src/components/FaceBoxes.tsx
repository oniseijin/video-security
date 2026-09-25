export interface FaceBox {
  x: number
  y: number
  w: number
  h: number
}

export function toFaceBoxes(faces: number[][]): FaceBox[] {
  return faces
    .filter((box) => box.length === 4)
    .map((box) => ({ x: box[0], y: box[1], w: box[2], h: box[3] }))
}

export function FaceBoxes({
  boxes,
  className = "face-box",
}: {
  boxes: FaceBox[]
  className?: string
}) {
  return (
    <>
      {boxes.map((box, i) => (
        <span
          className={className}
          key={i}
          style={{
            left: `${box.x * 100}%`,
            top: `${box.y * 100}%`,
            width: `${box.w * 100}%`,
            height: `${box.h * 100}%`,
          }}
        />
      ))}
    </>
  )
}
