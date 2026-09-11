// Adapted from David Haz / React Bits Noise (MIT + Commons Clause).
// Static 256px tile rather than a continuous full-screen animation.
export function texture(canvas) {
  const ctx = canvas.getContext('2d', { alpha: true });
  if (!ctx) return;
  canvas.width = canvas.height = 256;
  const pixels = ctx.createImageData(256, 256);
  for (let i = 0; i < pixels.data.length; i += 4) {
    const value = Math.random() * 255;
    pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = value;
    pixels.data[i + 3] = 15;
  }
  ctx.putImageData(pixels, 0, 0);
}

