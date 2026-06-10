/**
 * Products store – manages product_category and product CRUD (M4 step 3).
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { get, post, put, del } from '../api/http'
import { ApiError } from '../api/http'
import type { components } from '../api/schema'

type ProductCategoryRead = components['schemas']['ProductCategoryRead']
type ProductCategoryWrite = components['schemas']['ProductCategoryWrite']
type ProductCategoryListResponse = components['schemas']['ProductCategoryListResponse']
type ProductRead = components['schemas']['ProductRead']
type ProductWrite = components['schemas']['ProductWrite']
type ProductListResponse = components['schemas']['ProductListResponse']

export const useProductsStore = defineStore('products', () => {
  // --- Categories ---
  const categories = ref<ProductCategoryRead[]>([])
  const loadingCategories = ref(false)
  const categoryError = ref<string | null>(null)

  async function fetchCategories() {
    loadingCategories.value = true
    categoryError.value = null
    try {
      const data = await get<ProductCategoryListResponse>('/api/v1/product-categories')
      categories.value = data.items
    } catch (e: unknown) {
      categoryError.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loadingCategories.value = false
    }
  }

  async function createCategory(data: ProductCategoryWrite): Promise<ProductCategoryRead> {
    const result = await post<ProductCategoryRead>('/api/v1/product-categories', data)
    await fetchCategories()
    return result
  }

  async function updateCategory(id: string, data: ProductCategoryWrite): Promise<ProductCategoryRead> {
    const result = await put<ProductCategoryRead>(`/api/v1/product-categories/${id}`, data)
    await fetchCategories()
    return result
  }

  async function deleteCategory(id: string): Promise<void> {
    await del(`/api/v1/product-categories/${id}`)
    await fetchCategories()
  }

  // --- Products ---
  const items = ref<ProductRead[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const query = ref('')
  const categoryFilter = ref<string | null>(null)
  const limit = ref(50)
  const offset = ref(0)
  const sortBy = ref<'name' | 'created_at'>('created_at')

  async function fetchProducts() {
    loading.value = true
    error.value = null
    try {
      const params = new URLSearchParams()
      if (query.value) params.set('q', query.value)
      if (categoryFilter.value) params.set('category_id', categoryFilter.value)
      params.set('limit', String(limit.value))
      params.set('offset', String(offset.value))
      params.set('sort_by', sortBy.value)
      const url = `/api/v1/products?${params.toString()}`
      const data = await get<ProductListResponse>(url)
      items.value = data.items
      total.value = data.total
    } catch (e: unknown) {
      error.value = e instanceof ApiError ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function fetchProduct(id: string): Promise<ProductRead> {
    return await get<ProductRead>(`/api/v1/products/${id}`)
  }

  async function createProduct(data: ProductWrite): Promise<ProductRead> {
    const result = await post<ProductRead>('/api/v1/products', data)
    await fetchProducts()
    return result
  }

  async function updateProduct(id: string, data: ProductWrite): Promise<ProductRead> {
    const result = await put<ProductRead>(`/api/v1/products/${id}`, data)
    await fetchProducts()
    return result
  }

  async function deleteProduct(id: string): Promise<void> {
    await del(`/api/v1/products/${id}`)
    await fetchProducts()
  }

  return {
    // categories
    categories,
    loadingCategories,
    categoryError,
    fetchCategories,
    createCategory,
    updateCategory,
    deleteCategory,
    // products
    items,
    total,
    loading,
    error,
    query,
    categoryFilter,
    limit,
    offset,
    sortBy,
    fetchProducts,
    fetchProduct,
    createProduct,
    updateProduct,
    deleteProduct,
  }
})
