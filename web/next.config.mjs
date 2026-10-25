/** Static export: the same build is served by the cloud web host and bundled into
 * the Tauri desktop app, so both clients share one UI and one versioned API. */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};
export default nextConfig;
