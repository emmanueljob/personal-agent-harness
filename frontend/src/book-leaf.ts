import * as THREE from 'three';

// Integrate tangents across each strip of paper. Keeping every strip the
// same length lets the sheet curl around the gutter without stretching its art.
export function createLeaf(width: number, height: number) {
  const columns = 32, rows = 8;
  const geometry = new THREE.PlaneGeometry(width, height, columns, rows);
  const position = geometry.attributes.position as THREE.BufferAttribute;
  position.setUsage(THREE.DynamicDrawUsage);
  const front = new THREE.MeshStandardMaterial({roughness: .94});
  const back = new THREE.MeshStandardMaterial({roughness: .94, side: THREE.BackSide});
  const group = new THREE.Group();
  for (const material of [front, back]) {
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    group.add(mesh);
  }
  // The reverse is read from the fore-edge towards the binding.
  const reverseUV = geometry.attributes.uv.clone();
  for (let i = 0; i < reverseUV.count; i++) reverseUV.setX(i, 1 - reverseUV.getX(i));
  geometry.setAttribute('uv1', reverseUV);

  function bend(progress: number, corner: number) {
    const arc = Math.sin(progress * Math.PI);
    for (let row = 0; row <= rows; row++) {
      const v = row / rows;
      let x = 0, z = .012;
      for (let col = 0; col <= columns; col++) {
        const u = col / columns;
        position.setXYZ(row * (columns + 1) + col, x, height * (.5 - v), z);
        const roll = .82 * Math.sin(Math.PI * (u - .48)) + .35 * corner * (v - .5) * u;
        const angle = Math.PI * progress + arc * roll;
        x += Math.cos(angle) * width / columns;
        z += Math.sin(angle) * width / columns;
      }
    }
    position.needsUpdate = true;
    geometry.computeVertexNormals();
  }
  return {group, front, back, bend};
}
