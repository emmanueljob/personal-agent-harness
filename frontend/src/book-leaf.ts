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

  function bend(progress: number, corner: number, stackFraction?: number) {
    const arc = Math.sin(progress * Math.PI);
    for (let row = 0; row <= rows; row++) {
      const v = row / rows;
      let x = 0, z = .012;
      for (let col = 0; col <= columns; col++) {
        const u = col / columns;
        // Ease the lifted sheet into the exact camber of either resting stack.
        const edgeDepth = stackFraction === undefined ? 0 : .054 + .048 * stackFraction;
        const restingLift = stackFraction === undefined ? 0 :
          paperArch(u) + u * ((2*progress-1)*edgeDepth + progress*.088);
        position.setXYZ(row * (columns + 1) + col, x, height * (.5 - v), z + restingLift);
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

// Build a stack from the surface of its board up to a cambered sheet. The
// side walls share the same profile as the printed face, so the paper edges
// meet the board and printed face even when the camera views them obliquely.
export function paperArch(u: number) {
  return .175 * Math.sin(Math.PI * u) * Math.exp(-1.1 * u);
}

export function createPageStack(width: number, height: number, left: boolean) {
  const geometry = new THREE.BoxGeometry(width, height, 1, 36, 2, 1);
  geometry.translate(width / 2, 0, 0);
  const positions = geometry.attributes.position;
  const mix = Array.from({length: positions.count}, (_, i) => positions.getZ(i) + .5);
  const arch = Array.from({length: positions.count}, (_, i) => paperArch(positions.getX(i) / width));
  let lastOpen = -1, lastFraction = -1;

  function update(open: number, fraction: number) {
    if (Math.abs(lastOpen-open)<.0001 && Math.abs(lastFraction-fraction)<.0001) return;
    lastOpen=open;lastFraction=fraction;
    const leftEdges=.054 + .048*fraction;
    for(let i=0;i<positions.count;i++){
      const u=positions.getX(i)/width;
      const depth=left ?
        .015+(leftEdges*u+arch[i])*open :
        .22+(arch[i]-leftEdges*u)*open;
      positions.setZ(i,left ? -.025-depth*(1-mix[i]) : -.11+depth*mix[i]);
    }
    positions.needsUpdate=true;
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();
    geometry.computeBoundingBox();
  }
  return {geometry, update};
}
