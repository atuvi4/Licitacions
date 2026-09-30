"use client";

/**
 * DropdownMenu de shadcn/ui (sobre Radix), amb les classes Tuvi.
 *
 * Per a les accions d'una fila. Radix hi posa el rol `menu`, la navegació amb
 * fletxes, Escape i el focus retornat al botó que l'ha obert.
 */

import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { ChevronRight } from "lucide-react";
import type { ComponentProps } from "react";

import { cn } from "../cn";

export const DropdownMenu = DropdownMenuPrimitive.Root;
export const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;
export const DropdownMenuGroup = DropdownMenuPrimitive.Group;
export const DropdownMenuPortal = DropdownMenuPrimitive.Portal;
export const DropdownMenuSub = DropdownMenuPrimitive.Sub;

const ITEM_CLASS =
  "relative flex min-h-9 cursor-default items-center gap-2 rounded-[4px] px-2.5 py-1.5 text-[13.5px] outline-none select-none data-[highlighted]:bg-subtle data-[disabled]:pointer-events-none data-[disabled]:opacity-45";
const PANEL_CLASS =
  "animate-fade-in z-50 min-w-[11rem] overflow-hidden rounded-control border border-border bg-surface p-1 text-body text-ink shadow-dialog";

/** Un submenú (p. ex. «Canviar estat ›»). */
export function DropdownMenuSubTrigger({ className, children, ...props }: ComponentProps<typeof DropdownMenuPrimitive.SubTrigger>) {
  return (
    <DropdownMenuPrimitive.SubTrigger
      data-slot="dropdown-menu-sub-trigger"
      className={cn(ITEM_CLASS, "text-ink data-[state=open]:bg-subtle", className)}
      {...props}
    >
      {children}
      <ChevronRight size={14} aria-hidden className="ml-auto text-muted" />
    </DropdownMenuPrimitive.SubTrigger>
  );
}

export function DropdownMenuSubContent({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.SubContent>) {
  return (
    <DropdownMenuPortal>
      <DropdownMenuPrimitive.SubContent data-slot="dropdown-menu-sub-content" className={cn(PANEL_CLASS, className)} {...props} />
    </DropdownMenuPortal>
  );
}

export function DropdownMenuContent({
  className,
  sideOffset = 4,
  align = "end",
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Content>) {
  return (
    <DropdownMenuPortal>
      <DropdownMenuPrimitive.Content
        data-slot="dropdown-menu-content"
        sideOffset={sideOffset}
        align={align}
        className={cn(
          "animate-fade-in z-50 min-w-[11rem] overflow-hidden rounded-control border border-border bg-surface p-1 text-body text-ink shadow-dialog",
          className,
        )}
        {...props}
      />
    </DropdownMenuPortal>
  );
}

export function DropdownMenuItem({
  className,
  destructive = false,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Item> & { destructive?: boolean }) {
  return (
    <DropdownMenuPrimitive.Item
      data-slot="dropdown-menu-item"
      data-destructive={destructive ? "true" : undefined}
      className={cn(
        "relative flex min-h-9 cursor-default items-center gap-2 rounded-[4px] px-2.5 py-1.5 text-[13.5px] outline-none select-none",
        "data-[highlighted]:bg-subtle data-[disabled]:pointer-events-none data-[disabled]:opacity-45",
        destructive ? "text-danger data-[highlighted]:bg-danger-soft" : "text-ink",
        className,
      )}
      {...props}
    />
  );
}

export function DropdownMenuLabel({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.Label>) {
  return (
    <DropdownMenuPrimitive.Label
      data-slot="dropdown-menu-label"
      className={cn("px-2.5 pt-1.5 pb-1 font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase", className)}
      {...props}
    />
  );
}

export function DropdownMenuSeparator({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return (
    <DropdownMenuPrimitive.Separator
      data-slot="dropdown-menu-separator"
      className={cn("-mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  );
}
