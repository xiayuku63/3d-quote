// ── Three.js 3D Viewer ──
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { normalizeColorToken } from './state.js';

let scene, camera, renderer, controls, stlLoader, currentMesh;
let previewContainer, previewPlaceholder;

export function initViewer(previewContainerEl, previewPlaceholderEl) {
    previewContainer = previewContainerEl;
    previewPlaceholder = previewPlaceholderEl;

    scene = new THREE.Scene();
    scene.background = new THREE.Color(0xffffff);

    camera = new THREE.PerspectiveCamera(45, previewContainer.clientWidth / previewContainer.clientHeight, 0.1, 10000);
    camera.position.set(0, 0, 120);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setSize(previewContainer.clientWidth, previewContainer.clientHeight);
    previewContainer.appendChild(renderer.domElement);

    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
    dirLight.position.set(80, 80, 120);
    scene.add(dirLight);

    const gridHelper = new THREE.GridHelper(200, 20, 0x334155, 0x1e293b);
    gridHelper.position.y = -40;
    scene.add(gridHelper);

    stlLoader = new STLLoader();
    initialised = true;

    function animate() {
        requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
    }
    animate();
}

function fitCameraToMesh(meshObject) {
    const box = new THREE.Box3().setFromObject(meshObject);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    const fov = camera.fov * (Math.PI / 180);
    let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));
    cameraZ *= 1.6;
    camera.position.set(center.x, center.y, center.z + cameraZ);
    camera.near = Math.max(maxDim / 100, 0.1);
    camera.far = Math.max(maxDim * 20, 1000);
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.update();
}

function clearCurrentMesh() {
    if (!currentMesh) return;
    scene.remove(currentMesh);
    currentMesh.geometry.dispose();
    currentMesh.material.dispose();
    currentMesh = null;
}

function getRenderColorHex(colorKey) {
    const key = normalizeColorToken(colorKey);
    const palette = {
        White: 0xf3f4f6, Black: 0x111827, Gray: 0x9ca3af, Red: 0xef4444,
        Blue: 0x3b82f6, Green: 0x22c55e, Yellow: 0xeab308, Orange: 0xf97316,
        Purple: 0xa855f7, Pink: 0xec4899, Brown: 0x8b5e3c,
    };
    if (palette[key]) return palette[key];
    const hash = Array.from(key || 'Blue').reduce((h, c) => ((h << 5) - h + c.charCodeAt(0)) | 0, 0);
    return Math.abs(hash) % 0xffffff;
}

export function renderSTL(file, colorKey = 'Blue') {
    const reader = new FileReader();
    reader.onload = (event) => {
        try {
            const geometry = stlLoader.parse(event.target.result);
            geometry.computeVertexNormals();
            geometry.center();
            clearCurrentMesh();
            const material = new THREE.MeshStandardMaterial({
                color: getRenderColorHex(colorKey), metalness: 0.15, roughness: 0.65,
            });
            currentMesh = new THREE.Mesh(geometry, material);
            currentMesh.rotation.set(0, 0, 0);
            scene.add(currentMesh);
            fitCameraToMesh(currentMesh);
            previewPlaceholder.classList.add('hidden');
        } catch (e) {
            previewPlaceholder.textContent = '预览失败，请确认 STL 文件有效';
            previewPlaceholder.classList.remove('hidden');
        }
    };
    reader.readAsArrayBuffer(file);
}

export function buildPlaceholderThumbnail(ext) {
    const label = (ext || 'file').toUpperCase();
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120" viewBox="0 0 200 120">
        <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#ffffff"/><stop offset="100%" stop-color="#f8fafc"/>
        </linearGradient></defs>
        <rect width="200" height="120" rx="8" fill="url(#g)"/>
        <rect x="12" y="12" width="176" height="96" rx="6" fill="none" stroke="#cbd5e1"/>
        <text x="100" y="62" text-anchor="middle" fill="#334155" font-size="18" font-family="Arial,sans-serif" font-weight="700">${label}</text>
        <text x="100" y="84" text-anchor="middle" fill="#64748b" font-size="11" font-family="Arial,sans-serif">Static Preview</text>
    </svg>`;
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function updateViewerSize() {
    if (!renderer || !previewContainer) return;
    camera.aspect = previewContainer.clientWidth / previewContainer.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(previewContainer.clientWidth, previewContainer.clientHeight);
}
