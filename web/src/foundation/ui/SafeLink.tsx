import { forwardRef } from "react";
import { Link, useInRouterContext, type LinkProps } from "react-router-dom";

/** A router link that still renders a plain anchor when a component is shown outside a router (previews, tests). */
export const SafeLink = forwardRef<HTMLAnchorElement, LinkProps>(function SafeLink(props, ref) {
  const inRouter = useInRouterContext();
  if (inRouter) return <Link ref={ref} {...props} />;
  const { to, className, children, id, title, target, rel } = props;
  return (
    <a ref={ref} href={typeof to === "string" ? to : (to.pathname ?? "")} className={typeof className === "string" ? className : undefined} id={id} title={title} target={target} rel={rel} aria-label={props["aria-label"]}>
      {typeof children === "function" ? null : children}
    </a>
  );
});
