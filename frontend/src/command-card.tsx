import {motion, useIsPresent, useReducedMotion, usePresenceData} from 'motion/react';
import {useEffect, useRef, type ReactNode, type RefObject} from 'react';

type Props = {
  children: ReactNode;
  id: string;
  direction: number;
  locked: boolean;
  cardRef: RefObject<HTMLElement | null>;
  onEntered: (id: string) => void;
  onEntering: () => void;
};

export function CommandCard({children, id, direction, locked, cardRef, onEntered, onEntering}: Props) {
  const present = useIsPresent();
  const focused = useRef(false);
  useEffect(() => {
    if (present && !locked && !focused.current && cardRef.current?.id==='cc-'+id) {
      cardRef.current.focus({preventScroll:true});
      focused.current=true;
    }
  }, [present, locked, id, cardRef]);
  const reducedMotion = useReducedMotion();
  const liveDirection = (usePresenceData() as number | undefined) ?? direction;
  return <motion.article ref={cardRef} className="cc-card" id={'cc-'+id} tabIndex={-1}
    aria-labelledby={'cc-title-'+id} aria-hidden={!present} inert={!present || locked}
    initial={{opacity: reducedMotion ? 1 : 0, x: reducedMotion ? 0 : direction*24, scale: reducedMotion ? 1 : .985}}
    animate={{opacity:1, x:0, scale:1}}
    exit={{opacity: reducedMotion ? 1 : 0, x: reducedMotion ? 0 : -liveDirection*24, scale: reducedMotion ? 1 : .985}}
    transition={{duration: reducedMotion ? 0 : .18, ease:[.22,1,.36,1]}}
    onAnimationStart={() => {if (present && !reducedMotion) onEntering()}}
    onAnimationComplete={() => {if (present) onEntered(id)}}>
    {children}
  </motion.article>;
}
