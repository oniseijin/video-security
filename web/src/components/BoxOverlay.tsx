import type { EventKeyframeBox } from "../api"
import { FaceBoxes } from "./FaceBoxes"
import type { FaceBox } from "./FaceBoxes"

function kindBoxes(
  boxes: EventKeyframeBox[] | undefined,
  kind: "person" | "animal"
): FaceBox[] {
  if (!boxes) {
    return []
  }
  return boxes
    .filter((b) => b.kind === kind && b.box.length === 4)
    .map((b) => ({ x: b.box[0], y: b.box[1], w: b.box[2], h: b.box[3] }))
}

export function BoxOverlay({ boxes }: { boxes?: EventKeyframeBox[] }) {
  return (
    <>
      <FaceBoxes className="person-box" boxes={kindBoxes(boxes, "person")} />
      <FaceBoxes className="animal-box" boxes={kindBoxes(boxes, "animal")} />
    </>
  )
}
