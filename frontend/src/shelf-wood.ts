// A seamless, quarter-sawn veneer. Draw once for the whole case, including its rails.
let veneer: string | undefined;

export function walnutVeneer() {
  if (veneer) return veneer;
  const tile = document.createElement('canvas');
  tile.width = 640;
  tile.height = 160;
  const ctx = tile.getContext('2d');
  if (!ctx) return 'none';
  const pixels = ctx.createImageData(tile.width, tile.height);
  let seed = 24731;
  for (let y = 0; y < tile.height; y++) {
    for (let x = 0; x < tile.width; x++) {
      const a = 2 * Math.PI * x / tile.width;
      const b = 2 * Math.PI * y / tile.height;
      const wave = b + .074 * Math.sin(2 * a + b) + .038 * Math.cos(3 * a - 2 * b);
      const ring = 2.2 * Math.sin(13 * wave) + 3.1 * Math.cos(6 * wave) + 4 * Math.sin(3 * wave);
      seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
      const pores = 6 * (seed / 4294967296 - .5) - 7 * Math.pow(.5 + .5 * Math.cos(54 * wave), 7);
      const index = 4 * (y * tile.width + x);
      pixels.data[index] = 92 + ring + pores;
      pixels.data[index + 1] = 58 + .8 * ring + .8 * pores;
      pixels.data[index + 2] = 39 + .56 * ring + .65 * pores;
      pixels.data[index + 3] = 255;
    }
  }
  ctx.putImageData(pixels, 0, 0);
  veneer = 'url(' + tile.toDataURL() + ')';
  return veneer;
}
