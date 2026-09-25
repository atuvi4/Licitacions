/**
 * Components base del redisseny (handoff Sprint 1, secció 2).
 *
 * `@/components/ui` (el barril `ui.tsx`) reexporta tot això perquè cap import
 * anterior es trenqui; els components nous s'importen d'aquí o del barril.
 */
export { cn } from "./cn";
export { Button, LinkButton, type ButtonSize, type ButtonVariant } from "./Button";
export { StatusPill, type Level, type StatusPillProps } from "./StatusPill";
export { Attribution, type AttributionKind, type AttributionProps } from "./Attribution";
export { EvidenceRef, type EvidenceRefProps } from "./EvidenceRef";
export { Banner, type BannerLevel, type BannerProps } from "./Banner";
export { Card, CardHeader, PageHeader } from "./Card";
export { EmptyState, type EmptyStateProps } from "./EmptyState";
export { LoadErrorState, type LoadErrorStateProps } from "./LoadErrorState";
export { ErrorState, type ErrorStateProps } from "./ErrorState";
export { PermissionNote, type PermissionNoteProps } from "./PermissionNote";
export { PageSkeleton, type PageSkeletonProps, type PageSkeletonVariant } from "./PageSkeleton";
export { Tabs, type TabItem, type TabsProps } from "./Tabs";
export { Drawer, type DrawerProps } from "./Drawer";
export {
  DataTable,
  type DataTableColumn,
  type DataTableGroup,
  type DataTableProps,
  type RowTint,
} from "./DataTable";
export { Checkbox, Field, Input, Select, Textarea } from "./Form";
export { Spinner } from "./Spinner";
export { ErrorNotice, LoadingRows, ResultNotice, Skeleton } from "./Feedback";
