// HYPOTHESIS: null strategy — behaves exactly like vanilla Petra (control).
// PREDICTION: (none — baseline)
/**
 * Couche stratégie du bot (oad-lab, SPEC.md chantier D).
 *
 * C'est CE fichier que la boucle d'évolution fait muter — plus
 * config.js. `update` est appelée à chaque tour d'IA (~toutes les
 * 2,4 s de jeu), AVANT l'update du quartier général de Petra, et peut
 * réécrire N'IMPORTE QUEL champ de `config` en place : Petra relit sa
 * Config en continu, les changements prennent effet immédiatement et
 * peuvent donc dépendre de l'état du jeu — timings conditionnels,
 * bascules éco/militaire, réactions à l'ennemi.
 *
 * Contrat :
 * - garder l'export `Strategy` et la signature de `update` ;
 * - rapide (elle tourne dans la boucle de simulation) ;
 * - les exceptions sont attrapées par l'appelant (_petrabot.js) : un
 *   crash ne coûte que l'ajustement du tour, pas la partie ;
 * - `this.mem` est une mémoire persistante libre entre les tours
 *   (non sérialisée : parties headless d'une traite uniquement).
 *
 * Sans rapport avec startingStrategy.js (analyse ponctuelle de la
 * position de départ, héritée de Petra) : cette couche-ci est
 * appelée en continu et pilote la Config.
 */

export function Strategy(config)
{
	// Mémoire de travail entre les tours (compteurs, drapeaux,
	// valeurs de référence de la config vanilla...).
	this.mem = {};
}

/**
 * @param gameState — l'état du jeu côté candidat (API common-api) ;
 * @param config — la Config vivante de Petra, modifiable en place.
 */
Strategy.prototype.update = function(gameState, config)
{
	// Stratégie nulle : aucun ajustement, comportement vanilla.
};
