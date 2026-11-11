/** Exact-sheet app icon crop (opaque navy tile). Prefer this over the hand SVG. */
export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <img
      src="/brand/app_icon.png"
      width={size}
      height={size}
      alt="MOR"
      className="mor-brand-mark"
      style={{ width: size, height: size, flex: "none" }}
    />
  );
}
