import type { CSSProperties } from "react";

/** Hand-drawn sprite icons from MASTER (same symbols as the vanilla dashboard). */
export function Icon({
  name,
  size = 18,
  className,
  style,
}: {
  name: string;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      className={className ? `mor-icon ${className}` : "mor-icon"}
      width={size}
      height={size}
      aria-hidden="true"
      style={style}
    >
      <use href={`#i-${name}`} />
    </svg>
  );
}
