"use client";

import { useEffect, useRef, useState } from "react";
import { collection, doc, query, onSnapshot, orderBy, where, limit, QueryConstraint } from "firebase/firestore";
import { db } from "@/lib/firebase";
import CreativeCard from "@/components/CreativeCard";
import { triggerPipeline } from "@/lib/pipelines";

interface Creative {
  ad_id: string;
  platform: string;
  ad_name: string;
  headline: string;
  ad_text: string;
  firebase_storage_url: string;
  final_url: string;
  updated_at: any;
}

interface SyncStatus {
  status: "running" | "complete" | "error";
  message: string;
  synced: number;
}

export default function Home() {
  const [creatives, setCreatives] = useState<Creative[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("All");
  const [syncStatuses, setSyncStatuses] = useState<Record<string, SyncStatus>>({});
  const statusUnsubs = useRef<Record<string, () => void>>({});

  const handleRefresh = (platform: string) => {
    const p = platform.toLowerCase() as "meta" | "bing" | "google";
    triggerPipeline(p);

    // Unsubscribe any previous listener for this platform
    statusUnsubs.current[p]?.();

    const unsubscribe = onSnapshot(doc(db, "pipeline_status", p), (snap) => {
      if (!snap.exists()) return;
      const data = snap.data() as SyncStatus;
      setSyncStatuses((prev) => ({ ...prev, [p]: data }));

      if (data.status === "complete" || data.status === "error") {
        setTimeout(() => {
          setSyncStatuses((prev) => {
            const next = { ...prev };
            delete next[p];
            return next;
          });
          statusUnsubs.current[p]?.();
          delete statusUnsubs.current[p];
        }, 8000);
      }
    });

    statusUnsubs.current[p] = unsubscribe;
  };

  // Cleanup status listeners on unmount
  useEffect(() => {
    return () => {
      Object.values(statusUnsubs.current).forEach((u) => u());
    };
  }, []);

  useEffect(() => {
    setLoading(true);
    const constraints: QueryConstraint[] = [orderBy("updated_at", "desc"), limit(200)];
    if (filter !== "All") {
      constraints.unshift(where("platform", "==", filter));
    }
    const q = query(collection(db, "ad_creatives"), ...constraints);

    const unsubscribe = onSnapshot(q, (snapshot) => {
      const items: Creative[] = [];
      snapshot.forEach((d) => items.push(d.data() as Creative));
      setCreatives(items);
      setLoading(false);
    });

    return () => unsubscribe();
  }, [filter]);

  const platforms = ["All", "Meta", "Google", "Bing"];
  const activeStatuses = Object.entries(syncStatuses);

  return (
    <main className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-6 flex flex-col md:flex-row md:items-end md:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">Ad Creative Gallery</h1>
            <p className="text-gray-600">Explore active creatives across all your ad accounts.</p>
          </div>
          <div className="flex gap-2">
            {["Meta", "Google", "Bing"].map((p) => {
              const st = syncStatuses[p.toLowerCase()];
              const isRunning = st?.status === "running";
              return (
                <button
                  key={`refresh-${p}`}
                  onClick={() => handleRefresh(p)}
                  disabled={isRunning}
                  className="text-xs bg-gray-900 text-white px-3 py-2 rounded shadow-sm hover:bg-gray-800 disabled:opacity-50 flex items-center gap-2"
                >
                  {isRunning && (
                    <span className="inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  )}
                  {isRunning ? "Syncing..." : `Sync ${p}`}
                </button>
              );
            })}
          </div>
        </header>

        {/* Live sync status panel */}
        {activeStatuses.length > 0 && (
          <div className="mb-6 flex flex-col gap-2">
            {activeStatuses.map(([p, st]) => (
              <div
                key={p}
                className={`flex items-center gap-3 px-4 py-3 rounded-lg text-sm border ${
                  st.status === "complete"
                    ? "bg-green-50 border-green-200 text-green-800"
                    : st.status === "error"
                    ? "bg-red-50 border-red-200 text-red-800"
                    : "bg-blue-50 border-blue-200 text-blue-800"
                }`}
              >
                {st.status === "running" && (
                  <span className="inline-block w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin flex-shrink-0" />
                )}
                {st.status === "complete" && <span className="flex-shrink-0">✓</span>}
                {st.status === "error" && <span className="flex-shrink-0">✗</span>}
                <span className="font-semibold capitalize">{p}</span>
                <span>{st.message}</span>
                {st.status === "running" && st.synced > 0 && (
                  <span className="ml-auto font-medium tabular-nums">{st.synced} synced</span>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-wrap gap-2 mb-8">
          {platforms.map((p) => (
            <button
              key={p}
              onClick={() => setFilter(p)}
              className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                filter === p
                  ? "bg-blue-600 text-white shadow-sm"
                  : "bg-white text-gray-600 hover:bg-gray-100 border border-gray-200"
              }`}
            >
              {p}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="flex justify-center items-center h-64">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600" />
          </div>
        ) : creatives.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
            {creatives.map((creative) => (
              <CreativeCard key={`${creative.platform}_${creative.ad_id}`} creative={creative} />
            ))}
          </div>
        ) : (
          <div className="bg-white rounded-lg p-12 text-center border-2 border-dashed border-gray-200">
            <h3 className="text-lg font-medium text-gray-900 mb-1">No creatives found</h3>
            <p className="text-gray-500">Use the Sync buttons above to pull creatives from each platform.</p>
          </div>
        )}
      </div>
    </main>
  );
}
