"use client";

import { useEffect, useState } from "react";
import { collection, query, onSnapshot, orderBy } from "firebase/firestore";
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

export default function Home() {
  const [creatives, setCreatives] = useState<Creative[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("All");
  const [refreshing, setRefreshing] = useState<string | null>(null);

  const handleRefresh = async (platform: string) => {
    const p = platform.toLowerCase() as 'meta' | 'bing' | 'google';
    setRefreshing(platform);
    const result = await triggerPipeline(p);
    alert(result.message);
    setRefreshing(null);
  };

  useEffect(() => {
    const q = query(collection(db, "ad_creatives"), orderBy("updated_at", "desc"));
    
    const unsubscribe = onSnapshot(q, (snapshot) => {
      const items: Creative[] = [];
      snapshot.forEach((doc) => {
        items.push(doc.data() as Creative);
      });
      setCreatives(items);
      setLoading(false);
    });

    return () => unsubscribe();
  }, []);

  const filteredCreatives = filter === "All" 
    ? creatives 
    : creatives.filter(c => c.platform === filter);

  const platforms = ["All", "Meta", "Google", "Bing"];

  return (
    <main className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-7xl mx-auto">
        <header className="mb-10 flex flex-col md:flex-row md:items-end md:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">Ad Creative Gallery</h1>
            <p className="text-gray-600">Explore active creatives across all your ad accounts.</p>
          </div>
          <div className="flex gap-2">
            {["Meta", "Google", "Bing"].map((p) => (
              <button
                key={`refresh-${p}`}
                onClick={() => handleRefresh(p)}
                disabled={refreshing !== null}
                className="text-xs bg-gray-900 text-white px-3 py-2 rounded shadow-sm hover:bg-gray-800 disabled:opacity-50 flex items-center gap-2"
              >
                {refreshing === p ? "Syncing..." : `Sync ${p}`}
              </button>
            ))}
          </div>
        </header>

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
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
          </div>
        ) : filteredCreatives.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
            {filteredCreatives.map((creative) => (
              <CreativeCard key={`${creative.platform}_${creative.ad_id}`} creative={creative} />
            ))}
          </div>
        ) : (
          <div className="bg-white rounded-lg p-12 text-center border-2 border-dashed border-gray-200">
            <h3 className="text-lg font-medium text-gray-900 mb-1">No creatives found</h3>
            <p className="text-gray-500">Try running your extraction scripts to populate the gallery.</p>
          </div>
        )}
      </div>
    </main>
  );
}
