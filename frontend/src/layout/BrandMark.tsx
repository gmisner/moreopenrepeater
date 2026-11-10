/** MOR mark: mountain + lattice tower under blue signal arcs on brand navy. */
export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 256 256" role="img" aria-label="MOR mountain and radio signal logo" style={{ flex: "none" }}>
      <rect width="256" height="256" rx="48" fill="#0B1220" />
      <g fill="none" stroke="#0D6EFD" strokeWidth="11" strokeLinecap="round">
        <path d="M44 108 A84 84 0 0 1 212 108" />
        <path d="M69 108 A59 59 0 0 1 187 108" />
        <path d="M94 108 A34 34 0 0 1 162 108" />
      </g>
      <path
        fill="#E5E7EB"
        d="M18 198 72 132 96 158 128 108 158 148 188 118 238 198 196 170 170 188 148 154 128 178 108 160 84 188 70 168Z"
      />
      <g fill="#E5E7EB" stroke="#E5E7EB" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <path d="M128 92v78" />
        <path d="M118 108h20M114 124h28M118 140h20" fill="none" />
        <path d="M128 170l-14 22M128 170l14 22" fill="none" />
        <circle cx="128" cy="88" r="4" />
      </g>
    </svg>
  );
}
