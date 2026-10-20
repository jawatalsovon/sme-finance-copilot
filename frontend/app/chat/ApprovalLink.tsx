"use client";
import Link from "next/link";
import { Badge } from "@/components/ui";

export function ApprovalLink({ id }: { id: string }) {
  return (
    <Link href="/approvals">
      <Badge tone="warn">Draft {id.slice(0, 12)} waiting for approval →</Badge>
    </Link>
  );
}
