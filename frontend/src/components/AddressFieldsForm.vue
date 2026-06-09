<script setup lang="ts">
/**
 * Reusable address form component (M3 step 2).
 *
 * Renders European/Dutch structured address fields. Used by CustomerEdit and
 * (step 3) CompanyProfile. The parent binds a modelValue of AddressWrite shape
 * (without the "type" field – that's provided externally via props).
 */
import { useI18n } from 'vue-i18n'
import { NFormItem, NInput, NGrid, NGridItem } from 'naive-ui'

export interface AddressModel {
  street?: string | null
  house_number?: string | null
  house_number_addition?: string | null
  postal_code?: string | null
  city?: string | null
  province?: string | null
  country_code?: string | null
}

const props = defineProps<{
  modelValue: AddressModel
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: AddressModel): void
}>()

const { t } = useI18n()

function update(field: keyof AddressModel, value: string) {
  emit('update:modelValue', { ...props.modelValue, [field]: value || null })
}
</script>

<template>
  <div class="address-fields-form">
    <n-grid :cols="2" :x-gap="12" :y-gap="0">
      <!-- Street -->
      <n-grid-item :span="2">
        <n-form-item :label="t('address.street')">
          <n-input
            :value="modelValue.street ?? ''"
            :placeholder="t('address.streetPlaceholder')"
            @update:value="(v) => update('street', v)"
          />
        </n-form-item>
      </n-grid-item>

      <!-- House number + addition -->
      <n-grid-item>
        <n-form-item :label="t('address.houseNumber')">
          <n-input
            :value="modelValue.house_number ?? ''"
            :placeholder="t('address.houseNumberPlaceholder')"
            @update:value="(v) => update('house_number', v)"
          />
        </n-form-item>
      </n-grid-item>
      <n-grid-item>
        <n-form-item :label="t('address.houseNumberAddition')">
          <n-input
            :value="modelValue.house_number_addition ?? ''"
            :placeholder="t('address.houseNumberAdditionPlaceholder')"
            @update:value="(v) => update('house_number_addition', v)"
          />
        </n-form-item>
      </n-grid-item>

      <!-- Postal code + city -->
      <n-grid-item>
        <n-form-item :label="t('address.postalCode')">
          <n-input
            :value="modelValue.postal_code ?? ''"
            :placeholder="t('address.postalCodePlaceholder')"
            @update:value="(v) => update('postal_code', v)"
          />
        </n-form-item>
      </n-grid-item>
      <n-grid-item>
        <n-form-item :label="t('address.city')">
          <n-input
            :value="modelValue.city ?? ''"
            :placeholder="t('address.cityPlaceholder')"
            @update:value="(v) => update('city', v)"
          />
        </n-form-item>
      </n-grid-item>

      <!-- Province -->
      <n-grid-item>
        <n-form-item :label="t('address.province')">
          <n-input
            :value="modelValue.province ?? ''"
            :placeholder="t('address.provincePlaceholder')"
            @update:value="(v) => update('province', v)"
          />
        </n-form-item>
      </n-grid-item>

      <!-- Country/Region -->
      <n-grid-item>
        <n-form-item :label="t('address.countryRegion')">
          <n-input
            :value="modelValue.country_code ?? ''"
            :placeholder="t('address.countryRegionPlaceholder')"
            maxlength="2"
            style="max-width: 100px"
            @update:value="(v) => update('country_code', v)"
          />
        </n-form-item>
      </n-grid-item>
    </n-grid>
  </div>
</template>
