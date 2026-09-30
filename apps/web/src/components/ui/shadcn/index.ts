/**
 * Peces de shadcn/ui que el design system Tuvi no tenia (auditoria 2026-09-29):
 * diàlegs, confirmacions, menús d'accions i taules amb ordenació.
 *
 * No s'han generat amb el CLI (en Tailwind 4 reescriu `globals.css` amb el seu
 * tema i la seva capa base): és el codi dels components de shadcn, sobre Radix
 * i TanStack Table v8, amb les classes i els tokens Tuvi i el `cn` propi. Per a
 * botons, camps, targetes, pastilles, calaixos i llistats simples es continuen
 * fent servir els components Tuvi de `@/components/ui`.
 */

export {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "./dialog";
export {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "./alert-dialog";
export {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "./dropdown-menu";
export { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "./table";
export { SortableTable, type ColumnDef, type SortableTableProps } from "./sortable-table";
