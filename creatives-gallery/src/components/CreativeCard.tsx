"use client";

import React, { useState } from "react";
import { doc, updateDoc, serverTimestamp } from "firebase/firestore";
import { db } from "@/lib/firebase";

interface Creative {
  ad_id: string;
  platform: string;
  ad_name: string;
  headline: string;
  ad_text: string;
  firebase_storage_url: string;
  final_url: string;
  creative_type?: string;
  review_status?: "keep" | "remove" | "change";
  review_comment?: string;
}

export type ViewMode = "card" | "compact" | "expanded";

const DOC_PREFIX: Record<string, string> = {
  Meta: "meta",
  Google: "gads",
  Bing: "bing",
};

const STATUS_STYLES = {
  keep:   { border: "border-green-400",  ring: "ring-green-400",  bg: "bg-green-500",  label: "Keep",   text: "text-green-700",  lightBg: "bg-green-50"  },
  remove: { border: "border-red-400",    ring: "ring-red-400",    bg: "bg-red-500",    label: "Remove", text: "text-red-700",    lightBg: "bg-red-50"    },
  change: { border: "border-yellow-400", ring: "ring-yellow-400", bg: "bg-yellow-400", label: "Change", text: "text-yellow-700", lightBg: "bg-yellow-50" },
};

// ── Review bar ────────────────────────────────────────────────────────────────
function ReviewBar({ creative }: { creative: Creative }) {
  const [commenting, setCommenting] = useState(false);
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);

  const docId = `${DOC_PREFIX[creative.platform]}_${creative.ad_id}`;

  const saveReview = async (status: "keep" | "remove" | "change", reviewComment?: string) => {
    setSaving(true);
    try {
      await updateDoc(doc(db, "ad_creatives", docId), {
        review_status: status,
        review_comment: reviewComment ?? "",
        review_updated_at: serverTimestamp(),
      });
    } finally {
      setSaving(false);
    }
  };

  const handleChange = () => {
    setComment(creative.review_comment ?? "");
    setCommenting(true);
  };

  const submitComment = async () => {
    await saveReview("change", comment);
    setCommenting(false);
  };

  const current = creative.review_status;

  return (
    <div className="mt-3 pt-3 border-t border-gray-100">
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-gray-400 uppercase font-medium mr-1">Review</span>

        {/* Keep */}
        <button
          onClick={() => saveReview("keep")}
          disabled={saving}
          className={`text-xs px-2.5 py-1 rounded-full font-medium border transition-colors ${
            current === "keep"
              ? "bg-green-500 border-green-500 text-white"
              : "border-green-400 text-green-700 hover:bg-green-50"
          }`}
        >
          Keep
        </button>

        {/* Remove */}
        <button
          onClick={() => saveReview("remove")}
          disabled={saving}
          className={`text-xs px-2.5 py-1 rounded-full font-medium border transition-colors ${
            current === "remove"
              ? "bg-red-500 border-red-500 text-white"
              : "border-red-400 text-red-700 hover:bg-red-50"
          }`}
        >
          Remove
        </button>

        {/* Change */}
        <button
          onClick={handleChange}
          disabled={saving}
          className={`text-xs px-2.5 py-1 rounded-full font-medium border transition-colors ${
            current === "change"
              ? "bg-yellow-400 border-yellow-400 text-white"
              : "border-yellow-400 text-yellow-700 hover:bg-yellow-50"
          }`}
        >
          Change
        </button>
      </div>

      {/* Existing comment (read-only) */}
      {current === "change" && creative.review_comment && !commenting && (
        <div
          className="mt-2 text-xs text-yellow-800 bg-yellow-50 border border-yellow-200 rounded px-2.5 py-1.5 cursor-pointer hover:bg-yellow-100 transition-colors"
          onClick={handleChange}
          title="Click to edit comment"
        >
          {creative.review_comment}
        </div>
      )}

      {/* Comment input */}
      {commenting && (
        <div className="mt-2 flex flex-col gap-1.5">
          <textarea
            autoFocus
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Describe the requested change…"
            rows={2}
            className="w-full text-xs border border-yellow-300 rounded px-2.5 py-1.5 resize-none focus:outline-none focus:ring-1 focus:ring-yellow-400"
          />
          <div className="flex gap-1.5">
            <button
              onClick={submitComment}
              disabled={saving || !comment.trim()}
              className="text-xs bg-yellow-400 text-white px-3 py-1 rounded font-medium hover:bg-yellow-500 disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => setCommenting(false)}
              className="text-xs text-gray-500 px-3 py-1 rounded hover:bg-gray-100"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Shared helpers ────────────────────────────────────────────────────────────
function MediaBlock({ creative, className }: { creative: Creative; className: string }) {
  const isYouTube = creative.firebase_storage_url.startsWith("https://www.youtube.com/embed/");
  const isVideo = !isYouTube && creative.firebase_storage_url.endsWith(".mp4");

  if (creative.firebase_storage_url === "N/A") {
    return (
      <div className={`${className} flex items-center justify-center bg-gray-100 text-gray-400 italic text-xs`}>
        No Media
      </div>
    );
  }
  if (isYouTube) {
    return (
      <iframe
        src={creative.firebase_storage_url}
        className={className}
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowFullScreen
      />
    );
  }
  if (isVideo) {
    return <video src={creative.firebase_storage_url} className={`${className} object-contain`} controls muted />;
  }
  return <img src={creative.firebase_storage_url} alt={creative.ad_name} className={`${className} object-contain`} />;
}

function PlatformBadge({ platform, size = "sm" }: { platform: string; size?: "xs" | "sm" }) {
  return (
    <span
      className={`inline-flex items-center bg-gray-900 text-white font-bold rounded ${
        size === "xs" ? "text-[9px] px-1.5 py-0.5" : "text-[10px] px-2 py-1"
      }`}
    >
      {platform}
    </span>
  );
}

function LandingLink({ url }: { url: string }) {
  if (url === "N/A") return null;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:text-blue-800 text-xs font-medium whitespace-nowrap">
      View →
    </a>
  );
}

function statusBorderClass(status?: string) {
  if (status === "keep")   return "border-green-400";
  if (status === "remove") return "border-red-400";
  if (status === "change") return "border-yellow-400";
  return "border-gray-200";
}

// ── Card view ─────────────────────────────────────────────────────────────────
function CardView({ creative }: { creative: Creative }) {
  return (
    <div className={`bg-white rounded-lg shadow-md overflow-hidden border-2 hover:shadow-lg transition-shadow duration-300 ${statusBorderClass(creative.review_status)}`}>
      <div className="relative h-48 bg-gray-100 flex items-center justify-center overflow-hidden">
        <MediaBlock creative={creative} className="w-full h-full" />
        <div className="absolute top-2 right-2">
          <PlatformBadge platform={creative.platform} />
        </div>
      </div>

      <div className="p-4">
        <h3 className="text-sm font-semibold text-gray-900 line-clamp-1 mb-1">
          {creative.headline !== "N/A" ? creative.headline : creative.ad_name}
        </h3>
        <p className="text-xs text-gray-600 line-clamp-3 h-12 mb-3">
          {creative.ad_text !== "N/A" ? creative.ad_text : "No description available."}
        </p>

        <div className="flex justify-between items-center pt-2 border-t border-gray-100">
          <span className="text-[10px] text-gray-400 uppercase font-medium">ID: {creative.ad_id}</span>
          <LandingLink url={creative.final_url} />
        </div>

        <ReviewBar creative={creative} />
      </div>
    </div>
  );
}

// ── Compact list row ──────────────────────────────────────────────────────────
function CompactView({ creative }: { creative: Creative }) {
  const headline = creative.headline !== "N/A" ? creative.headline : creative.ad_name;
  const body = creative.ad_text !== "N/A" ? creative.ad_text : null;
  const status = creative.review_status;

  return (
    <div className={`bg-white border-l-4 border-r border-t border-b rounded-lg px-4 py-3 hover:bg-gray-50 transition-colors ${statusBorderClass(status)}`}>
      <div className="flex items-center gap-4">
        {/* Thumbnail */}
        <div className="w-12 h-12 flex-shrink-0 rounded overflow-hidden bg-gray-100">
          <MediaBlock creative={creative} className="w-full h-full" />
        </div>

        {/* Text */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-gray-900 truncate">{headline}</p>
          {body && <p className="text-xs text-gray-500 truncate mt-0.5">{body}</p>}
          {status === "change" && creative.review_comment && (
            <p className="text-xs text-yellow-700 truncate mt-0.5 italic">"{creative.review_comment}"</p>
          )}
        </div>

        {/* Meta + review */}
        <div className="flex items-center gap-3 flex-shrink-0">
          <PlatformBadge platform={creative.platform} size="xs" />
          <span className="text-[10px] text-gray-400 hidden sm:block">ID: {creative.ad_id}</span>
          <LandingLink url={creative.final_url} />
        </div>
      </div>

      <ReviewBar creative={creative} />
    </div>
  );
}

// ── Expanded list row ─────────────────────────────────────────────────────────
function ExpandedView({ creative }: { creative: Creative }) {
  const headline = creative.headline !== "N/A" ? creative.headline : creative.ad_name;

  return (
    <div className={`bg-white border-2 rounded-lg overflow-hidden hover:shadow-md transition-shadow flex gap-0 ${statusBorderClass(creative.review_status)}`}>
      {/* Image panel */}
      <div className="w-72 flex-shrink-0 bg-gray-100 flex items-center justify-center">
        <MediaBlock creative={creative} className="w-full h-full min-h-52 max-h-72 object-contain" />
      </div>

      {/* Content panel */}
      <div className="flex-1 p-6 flex flex-col justify-between min-w-0">
        <div>
          <div className="flex items-start justify-between gap-3 mb-3">
            <h3 className="text-base font-semibold text-gray-900 leading-snug">{headline}</h3>
            <PlatformBadge platform={creative.platform} />
          </div>

          {creative.ad_text !== "N/A" && (
            <p className="text-sm text-gray-700 leading-relaxed line-clamp-6">{creative.ad_text}</p>
          )}

          {creative.ad_name && creative.ad_name !== headline && (
            <p className="text-xs text-gray-400 mt-3">Ad name: {creative.ad_name}</p>
          )}
        </div>

        <div className="flex items-center justify-between pt-4 border-t border-gray-100 mt-4">
          <span className="text-xs text-gray-400 font-medium">ID: {creative.ad_id}</span>
          <LandingLink url={creative.final_url} />
        </div>

        <ReviewBar creative={creative} />
      </div>
    </div>
  );
}

// ── Public export ─────────────────────────────────────────────────────────────
export default function CreativeCard({ creative, view = "card" }: { creative: Creative; view?: ViewMode }) {
  if (view === "compact") return <CompactView creative={creative} />;
  if (view === "expanded") return <ExpandedView creative={creative} />;
  return <CardView creative={creative} />;
}
