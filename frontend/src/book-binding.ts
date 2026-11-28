const cloth = ['#355853', '#7e4234', '#30455b', '#886338', '#52415d', '#45533a', '#8c5049', '#334f65', '#72472e', '#565338'];

export function bindingColor(id: string) {
  let hash = 0;
  for (const character of id) hash = (Math.imul(31, hash) + character.charCodeAt(0)) >>> 0;
  return cloth[hash % cloth.length];
}
