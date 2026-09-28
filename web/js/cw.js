// Browser-side preview of the CW ID, using the same table and PARIS timing
// as src/dsp/morse.py so what you hear matches what the repeater sends.
const MORSE_CODE = {
  A: ".-", B: "-...", C: "-.-.", D: "-..", E: ".", F: "..-.", G: "--.", H: "....", I: "..",
  J: ".---", K: "-.-", L: ".-..", M: "--", N: "-.", O: "---", P: ".--.", Q: "--.-", R: ".-.",
  S: "...", T: "-", U: "..-", V: "...-", W: ".--", X: "-..-", Y: "-.--", Z: "--..",
  0: "-----", 1: ".----", 2: "..---", 3: "...--", 4: "....-",
  5: ".....", 6: "-....", 7: "--...", 8: "---..", 9: "----.",
  "/": "-..-.", ".": ".-.-.-", ",": "--..--", "?": "..--..", "=": "-...-",
};
const RAMP_SECONDS = 0.005;

let context = null;

export function playCW(text, wpm, toneHz) {
  context ??= new AudioContext();
  const unit = 1.2 / wpm;
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.frequency.value = toneHz;
  gain.gain.value = 0;
  oscillator.connect(gain).connect(context.destination);

  let t = context.currentTime + 0.05;
  const start = t;
  let started = false;
  for (const word of text.toUpperCase().split(" ")) {
    let wordStarted = false;
    for (const char of word) {
      const code = MORSE_CODE[char];
      if (!code) continue;
      if (wordStarted) t += 3 * unit;
      else if (started) t += 7 * unit;
      [...code].forEach((symbol, i) => {
        if (i > 0) t += unit;
        const length = (symbol === "." ? 1 : 3) * unit;
        gain.gain.setValueAtTime(0, t);
        gain.gain.linearRampToValueAtTime(0.3, t + RAMP_SECONDS);
        gain.gain.setValueAtTime(0.3, t + length - RAMP_SECONDS);
        gain.gain.linearRampToValueAtTime(0, t + length);
        t += length;
      });
      started = true;
      wordStarted = true;
    }
  }
  oscillator.start(start);
  oscillator.stop(t + 0.05);
}
