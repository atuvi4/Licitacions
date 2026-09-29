"use client";

/**
 * AlertDialog de shadcn/ui (sobre Radix), amb les classes Tuvi.
 *
 * Per a confirmacions que no es poden desfer d'un clic (desactivar un usuari,
 * retirar un rol). A diferència del Dialog, no es tanca clicant fora: cal
 * triar. Els botons són els `Button` Tuvi; «Cancel·la» rep el focus d'entrada.
 */

import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";
import type { ComponentProps } from "react";

import { Button, type ButtonVariant } from "../Button";
import { cn } from "../cn";

export const AlertDialog = AlertDialogPrimitive.Root;
export const AlertDialogTrigger = AlertDialogPrimitive.Trigger;
export const AlertDialogPortal = AlertDialogPrimitive.Portal;

export function AlertDialogOverlay({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Overlay>) {
  return (
    <AlertDialogPrimitive.Overlay
      data-slot="alert-dialog-overlay"
      className={cn("animate-fade-in fixed inset-0 z-50 bg-ink/45", className)}
      {...props}
    />
  );
}

export function AlertDialogContent({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Content>) {
  return (
    <AlertDialogPortal>
      <AlertDialogOverlay />
      <AlertDialogPrimitive.Content
        data-slot="alert-dialog-content"
        className={cn(
          "animate-fade-in fixed top-1/2 left-1/2 z-50 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2",
          "rounded-card border border-border bg-surface shadow-dialog focus:outline-none",
          className,
        )}
        {...props}
      />
    </AlertDialogPortal>
  );
}

export function AlertDialogHeader({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="alert-dialog-header" className={cn("px-5 pt-5", className)} {...props} />;
}

export function AlertDialogFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div data-slot="alert-dialog-footer" className={cn("flex flex-wrap items-center justify-end gap-2 px-5 py-4", className)} {...props} />
  );
}

export function AlertDialogTitle({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Title>) {
  return (
    <AlertDialogPrimitive.Title
      data-slot="alert-dialog-title"
      className={cn("font-heading text-[17px] leading-tight font-semibold text-ink", className)}
      {...props}
    />
  );
}

export function AlertDialogDescription({ className, ...props }: ComponentProps<typeof AlertDialogPrimitive.Description>) {
  return (
    <AlertDialogPrimitive.Description
      data-slot="alert-dialog-description"
      className={cn("mt-1.5 text-body text-ink", className)}
      {...props}
    />
  );
}

/** L'acció que es confirma. Primari per defecte: el text diu què fa, no el color. */
export function AlertDialogAction({
  variant = "primary",
  children,
  ...props
}: ComponentProps<typeof AlertDialogPrimitive.Action> & { variant?: ButtonVariant }) {
  return (
    <AlertDialogPrimitive.Action asChild {...props}>
      <Button variant={variant}>{children}</Button>
    </AlertDialogPrimitive.Action>
  );
}

export function AlertDialogCancel({ children = "Cancel·la", ...props }: ComponentProps<typeof AlertDialogPrimitive.Cancel>) {
  return (
    <AlertDialogPrimitive.Cancel asChild {...props}>
      <Button variant="secondary">{children}</Button>
    </AlertDialogPrimitive.Cancel>
  );
}
