export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 256 256" role="img" aria-label="MOR mountain and radio signal logo" style={{ flex: "none" }}>
      <g fill="none" stroke="#FF7A00" strokeWidth="13" strokeLinecap="round">
        <path d="M46 115 A82 82 0 0 1 210 115" />
        <path d="M70 115 A58 58 0 0 1 186 115" />
        <path d="M94 115 A34 34 0 0 1 162 115" />
      </g>
      <path fill="var(--mor-brand-mark, #F5F7FA)" d="M18 190 L85 116 L110 140 L145 97 L240 190 L190 165 L169 176 L146 143 L126 166 L108 151 L81 178 L70 157 Z" />
    </svg>
  );
}
