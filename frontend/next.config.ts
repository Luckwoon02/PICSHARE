import type { NextConfig } from "next";

// Falls back to localhost:8000 for local dev when BACKEND_HOSTNAME is not set
// (BACKEND_HOSTNAME is only injected by Docker Compose in production)
const backendHostname = process.env.BACKEND_HOSTNAME || "localhost:8000";

const nextConfig: NextConfig = {
  reactCompiler: true,
  allowedDevOrigins: [],

  // Proxy /api/* → backend so the frontend never hard-codes the backend URL
  rewrites: async () => [
    {
      source: "/api/:path*",
      destination: `http://${backendHostname}/:path*`,
    },
  ],

  // Allow Next.js <Image> to load photos served via S3 presigned URLs.
  // The wildcard covers any bucket name and any AWS region
  // e.g. my-bucket.s3.us-east-1.amazonaws.com
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "**.amazonaws.com",
        port: "",
        pathname: "/**",
      },
    ],
  },
};

export default nextConfig;
