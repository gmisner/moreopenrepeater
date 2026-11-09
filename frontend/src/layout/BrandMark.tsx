export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 256 256" role="img" aria-label="MOR mountain and radio signal logo" style={{ flex: "none" }}>
      <rect width="256" height="256" rx="48" fill="#0B1F2E" />
      <g fill="none" stroke="#FF7A00" strokeWidth="12" strokeLinecap="round">
        <path d="M44 115 A84 84 0 0 1 212 115" />
        <path d="M69 115 A59 59 0 0 1 187 115" />
        <path d="M94 115 A34 34 0 0 1 162 115" />
      </g>
      <path fill="#F5F7FA" d="M23 192 82 124 108 149 146 99 234 192 191 166 170 179 145 144 126 170 108 156 81 184 69 165Z" />
    </svg>
  );
}
