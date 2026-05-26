import {
  reactExtension,
  useApplyCartLinesChange,
  useCartLines,
  useSettings,
  BlockStack,
  Box,
  Checkbox,
  Heading,
  Text,
  Banner,
} from "@shopify/ui-extensions-react/checkout";
import { useState, useEffect } from "react";

// ID du variant "Adhésion LPL Club" — valeur par défaut, surchargeable via les settings de l'extension
const DEFAULT_MEMBERSHIP_VARIANT_ID = "gid://shopify/ProductVariant/55725365625217";

export default reactExtension("purchase.checkout.block.render", () => <LPLClubOffer />);

function LPLClubOffer() {
  const applyCartLinesChange = useApplyCartLinesChange();
  const cartLines = useCartLines();
  const settings = useSettings();

  const membershipVariantId = (settings.membership_variant_id as string) || DEFAULT_MEMBERSHIP_VARIANT_ID;

  // Vérifie si le produit est déjà dans le panier
  const isAlreadyInCart = cartLines.some(
    (line) => line.merchandise.id === membershipVariantId
  );

  const [checked, setChecked] = useState(isAlreadyInCart);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  // Sync l'état si le panier change en dehors du composant
  useEffect(() => {
    setChecked(isAlreadyInCart);
  }, [isAlreadyInCart]);

  async function handleChange(newChecked: boolean) {
    setLoading(true);
    setError(false);

    const result = await applyCartLinesChange(
      newChecked
        ? { type: "addCartLine", merchandiseId: membershipVariantId, quantity: 1 }
        : {
            type: "removeCartLine",
            id: cartLines.find((l) => l.merchandise.id === membershipVariantId)?.id ?? "",
            quantity: 1,
          }
    );

    if (result.type === "error") {
      setError(true);
      setChecked(!newChecked); // rollback
    } else {
      setChecked(newChecked);
    }

    setLoading(false);
  }

  return (
    <BlockStack border="base" borderRadius="base" padding="base" spacing="tight">
      <Heading level={3}>🎁 Rejoindre le LPL Club</Heading>
      <Text size="small" appearance="subdued">
        Profitez de <Text emphasis="bold">10% de réduction sur tous vos achats</Text> pendant 1 an.
      </Text>
      <Checkbox
        id="lpl-club-membership"
        checked={checked}
        onChange={handleChange}
        disabled={loading}
      >
        Ajouter l'adhésion LPL Club — <Text emphasis="bold">2,90€</Text>
      </Checkbox>
      {error && (
        <Banner status="critical">
          Une erreur est survenue. Veuillez réessayer.
        </Banner>
      )}
    </BlockStack>
  );
}
