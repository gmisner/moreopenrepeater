/** Transparent app icon (blue tile + alpha outside the squircle). */
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

/** Transparent horizontal lockup for the desktop nav. */
export function BrandLockup() {
  return (
    <img
      src="/brand/horizontal.png"
      alt="MOR — MoreOpenRepeater"
      className="mor-brand-lockup"
      width={434}
      height={145}
    />
  );
}
